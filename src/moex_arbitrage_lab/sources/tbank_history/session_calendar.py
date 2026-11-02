"""Frozen, data-driven MOEX derivatives session calendar for a bounded date range.

The calendar is loaded from a TOML table (``config/sources/tbank_history/screen_v0_2/
moex_calendar_2026_09_10.toml``) that the main agent compiled from official MOEX notices; no
session time is hard-coded here. Rules:

- a *regime* gives the venue-local (MSK, fixed ``utc_offset_hours``, no DST) windows that
  apply on every regular weekday of ``[effective_from, effective_to_exclusive)``; regimes must
  tile the coverage ``[coverage_start, coverage_end_exclusive)`` without gaps or overlaps;
- every window belongs to the trading day equal to its calendar date (MOEX trading day =
  calendar day since 2026-03-23; the evening session belongs to the current day);
- the opening auction is its own window with ``continuous = false``; its trades are parsed
  (never ``outside_verified_session``) but it is never a continuous session;
- Saturday/Sunday are never primary (weekend sessions belong to the next working day's
  trading day and are not requested); weekend facts are recorded for reporting only;
- per-date ``exceptions`` (``closed`` or ``modified``) remove a weekday from the primary
  universe by calendar, before any outcome is seen;
- any date outside the coverage is refused (``CalendarError``).
"""

import itertools
import tomllib
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from moex_arbitrage_lab.contracts.types import Identifier, SourceText
from moex_arbitrage_lab.sources.tbank_history.config import SessionWindow

DAY_STATUSES: Final = ("regular", "weekend", "closed", "modified")
DayStatus = Literal["regular", "weekend", "closed", "modified"]

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_NS_PER_S: Final = 1_000_000_000
_SATURDAY: Final = 5

HttpsUrl = Annotated[str, StringConstraints(max_length=2048, pattern=r"^https://[^\s]+$")]


class CalendarError(ValueError):
    """A date is outside the calendar coverage or not a regular primary day."""

    def __init__(self, reason: str, day: date) -> None:
        self.reason = reason
        self.day = day
        super().__init__(f"{reason}: {day.isoformat()}")


class WindowSpec(BaseModel):
    """One venue-local window ``[start_local, end_local)`` on the same calendar day."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    name: Identifier
    start_local: time
    end_local: time
    continuous: bool

    @model_validator(mode="after")
    def _within_one_day(self) -> Self:
        if self.start_local.tzinfo is not None or self.end_local.tzinfo is not None:
            raise ValueError("window times are venue-local times without an offset")
        if self.end_local <= self.start_local:
            raise ValueError("window must satisfy start_local < end_local on one calendar day")
        return self


def _check_windows(windows: tuple[WindowSpec, ...]) -> None:
    names = [window.name for window in windows]
    if len(set(names)) != len(names):
        raise ValueError("window names must be unique within a regime/exception")
    for left, right in itertools.pairwise(windows):
        if right.start_local < left.end_local:
            raise ValueError("windows must be sorted by start and must not overlap")
    if not any(window.continuous for window in windows):
        raise ValueError("a regime needs at least one continuous window")


class Regime(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    regime_id: Identifier
    effective_from: date
    effective_to_exclusive: date
    source_url: HttpsUrl
    windows: tuple[WindowSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.effective_to_exclusive <= self.effective_from:
            raise ValueError("regime must satisfy effective_from < effective_to_exclusive")
        _check_windows(self.windows)
        return self


class DateException(BaseModel):
    """A weekday that is not a regular primary day (holiday closure or modified schedule)."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    day: date
    kind: Literal["closed", "modified"]
    source_url: HttpsUrl
    note: SourceText


class WeekendFact(BaseModel):
    """A reported (non-primary) fact about one weekend date, e.g. a cancelled session."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    day: date
    status: Literal["no_weekend_session"]
    source_url: HttpsUrl


class MoexCalendar(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    calendar_id: Identifier
    venue: Literal["MOEX"]
    utc_offset_hours: int = Field(ge=-12, le=14)
    coverage_start: date
    coverage_end_exclusive: date
    trading_day_rule: Literal["calendar_day"]
    trading_day_rule_url: HttpsUrl
    weekend_policy: Literal["not_primary_not_requested"]
    weekend_policy_url: HttpsUrl
    holiday_regime_url: HttpsUrl
    regimes: tuple[Regime, ...] = Field(min_length=1)
    exceptions: tuple[DateException, ...] = ()
    weekend_facts: tuple[WeekendFact, ...] = ()

    @model_validator(mode="after")
    def _tiles_the_coverage(self) -> Self:
        if self.coverage_end_exclusive <= self.coverage_start:
            raise ValueError("coverage must satisfy coverage_start < coverage_end_exclusive")
        regimes = self.regimes
        for left, right in itertools.pairwise(regimes):
            if right.effective_from != left.effective_to_exclusive:
                raise ValueError("regimes must be sorted, contiguous and non-overlapping")
        if regimes[0].effective_from > self.coverage_start:
            raise ValueError("regimes do not cover coverage_start")
        if regimes[-1].effective_to_exclusive < self.coverage_end_exclusive:
            raise ValueError("regimes do not cover the end of the coverage")
        days = [entry.day for entry in self.exceptions]
        if len(set(days)) != len(days) or days != sorted(days):
            raise ValueError("exceptions must be unique and sorted by date")
        for day in days:
            if not self._covers(day) or day.weekday() >= _SATURDAY:
                raise ValueError("exceptions must be weekdays inside the coverage")
        facts = [entry.day for entry in self.weekend_facts]
        if len(set(facts)) != len(facts) or facts != sorted(facts):
            raise ValueError("weekend_facts must be unique and sorted by date")
        for day in facts:
            if not self._covers(day) or day.weekday() < _SATURDAY:
                raise ValueError("weekend_facts must be Saturdays/Sundays inside the coverage")
        return self

    def _covers(self, day: date) -> bool:
        return self.coverage_start <= day < self.coverage_end_exclusive

    def _require_covered(self, day: date) -> None:
        if not self._covers(day):
            raise CalendarError("outside_calendar_coverage", day)

    def regime_for(self, day: date) -> Regime:
        self._require_covered(day)
        for regime in self.regimes:
            if regime.effective_from <= day < regime.effective_to_exclusive:
                return regime
        raise CalendarError("outside_calendar_coverage", day)  # pragma: no cover - validated

    def day_status(self, day: date) -> DayStatus:
        """``regular`` (primary candidate), ``weekend``, ``closed`` or ``modified``."""
        self._require_covered(day)
        if day.weekday() >= _SATURDAY:
            return "weekend"
        for entry in self.exceptions:
            if entry.day == day:
                return entry.kind
        return "regular"

    def weekend_fact(self, day: date) -> WeekendFact | None:
        self._require_covered(day)
        return next((fact for fact in self.weekend_facts if fact.day == day), None)

    def exception_for(self, day: date) -> DateException | None:
        self._require_covered(day)
        return next((entry for entry in self.exceptions if entry.day == day), None)

    def sessions_for(self, day: date) -> tuple[SessionWindow, ...]:
        """All windows of a regular weekday as UTC ``SessionWindow`` values (auction included,
        flagged ``continuous=False``); every window's trading date is ``day``."""
        status = self.day_status(day)
        if status != "regular":
            raise CalendarError(f"not_a_regular_day:{status}", day)
        regime = self.regime_for(day)
        return tuple(
            SessionWindow(
                name=spec.name,
                start_utc_ns=self._utc_ns(day, spec.start_local),
                end_utc_ns=self._utc_ns(day, spec.end_local),
                trading_date=day,
                continuous=spec.continuous,
            )
            for spec in regime.windows
        )

    def continuous_sessions_for(self, day: date) -> tuple[SessionWindow, ...]:
        return tuple(window for window in self.sessions_for(day) if window.continuous)

    def _utc_ns(self, day: date, local: time) -> int:
        moment = datetime.combine(day, local, tzinfo=UTC) - timedelta(hours=self.utc_offset_hours)
        delta = moment - _EPOCH
        seconds = delta.days * 86_400 + delta.seconds
        return seconds * _NS_PER_S + delta.microseconds * 1_000


def _tuples(value: object) -> object:
    if isinstance(value, list):
        return tuple(_tuples(item) for item in value)
    if isinstance(value, dict):
        return {key: _tuples(item) for key, item in value.items()}
    return value


def load_calendar(path: Path) -> MoexCalendar:
    """Load the calendar TOML (TOML local times/dates); unknown keys are refused."""
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return MoexCalendar.model_validate(_tuples(data))
