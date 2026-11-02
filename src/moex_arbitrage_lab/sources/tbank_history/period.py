"""Deterministic multi-day request plan for one instrument (H-0001 v0.2 seconds screen).

A *period template* (``config/sources/tbank_history/screen_v0_2/gdz6_base.toml``) holds every
``TbankHistoryConfig`` field that is the same for all days (table ``[base]``) plus the
date-specific URL and raw filename templates, the period, the canary date and the calendar
file. ``build_day_config`` adds the date-specific ``archive_date``, ``url``, ``raw_filename``
and verified ``sessions`` (from the frozen calendar) and validates the result like any single-
day config.

``plan_requests`` is pure and offline: every calendar date of ``[start, end_exclusive)`` is
classified by the calendar *before* any data is seen. Regular weekdays are requested — the
canary first, then the remaining dates in chronological order. Weekends
(``weekend_not_requested``), exchange closures and modified schedules are listed separately
and never requested.
"""

import hashlib
import tomllib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Any, Final, Self

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from moex_arbitrage_lab.contracts.types import Identifier
from moex_arbitrage_lab.sources.tbank_history.acquire import config_sha256, raw_relative
from moex_arbitrage_lab.sources.tbank_history.config import TbankHistoryConfig
from moex_arbitrage_lab.sources.tbank_history.qa import iso_utc
from moex_arbitrage_lab.sources.tbank_history.session_calendar import (
    MoexCalendar,
    load_calendar,
)

PLAN_VERSION: Final = "tbank_period_plan_v1"
DATE_PLACEHOLDER: Final = "{date}"
NOT_REQUESTED_REASONS: Final = {
    "weekend": "weekend_not_requested",
    "closed": "exchange_closed_not_requested",
    "modified": "exceptional_session_not_requested",
}
# Supplied per date by ``build_day_config``; the template's ``[base]`` must not set them.
PER_DATE_FIELDS: Final = frozenset({"archive_date", "url", "raw_filename", "sessions"})

_Template = Annotated[str, StringConstraints(min_length=8, max_length=240)]
_CalendarFile = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]*\.toml$")]


class PeriodTemplate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    template_id: Identifier
    period_start: date
    period_end_exclusive: date
    canary_date: date
    calendar_file: _CalendarFile
    url_template: _Template
    raw_filename_template: _Template
    base: dict[str, Any]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.period_end_exclusive <= self.period_start:
            raise ValueError("period must satisfy period_start < period_end_exclusive")
        if not self.period_start <= self.canary_date < self.period_end_exclusive:
            raise ValueError("canary_date must lie inside the period")
        for name in ("url_template", "raw_filename_template"):
            if getattr(self, name).count(DATE_PLACEHOLDER) != 1:
                raise ValueError(f"{name} must contain {DATE_PLACEHOLDER} exactly once")
        clash = sorted(PER_DATE_FIELDS.intersection(self.base))
        if clash:
            raise ValueError(f"[base] must not set per-date fields: {clash}")
        return self


@dataclass(frozen=True, slots=True)
class LoadedTemplate:
    """A validated template with its calendar and the SHA-256 of both source files."""

    template: PeriodTemplate
    calendar: MoexCalendar
    template_sha256: str
    calendar_sha256: str


def _tuples(value: object) -> object:
    if isinstance(value, list):
        return tuple(_tuples(item) for item in value)
    if isinstance(value, dict):
        return {key: _tuples(item) for key, item in value.items()}
    return value


def load_template(path: Path) -> LoadedTemplate:
    """Load the template TOML and its calendar (``calendar_file``, same directory); validate a
    config for the canary date so a broken template fails before anything else."""
    template_bytes = path.read_bytes()
    template = PeriodTemplate.model_validate(_tuples(tomllib.loads(template_bytes.decode())))
    calendar_path = path.parent / template.calendar_file
    calendar_bytes = calendar_path.read_bytes()
    calendar = load_calendar(calendar_path)
    loaded = LoadedTemplate(
        template=template,
        calendar=calendar,
        template_sha256=hashlib.sha256(template_bytes).hexdigest(),
        calendar_sha256=hashlib.sha256(calendar_bytes).hexdigest(),
    )
    build_day_config(loaded, template.canary_date)
    return loaded


def build_day_config(loaded: LoadedTemplate, day: date) -> TbankHistoryConfig:
    """The single-day config of ``day`` (``CalendarError`` unless it is a regular day)."""
    template = loaded.template
    text = day.isoformat()
    values = {
        **template.base,
        "archive_date": day,
        "url": template.url_template.replace(DATE_PLACEHOLDER, text),
        "raw_filename": template.raw_filename_template.replace(DATE_PLACEHOLDER, text),
        "sessions": loaded.calendar.sessions_for(day),
    }
    return TbankHistoryConfig.model_validate(values)


@dataclass(frozen=True, slots=True)
class NotRequested:
    day: date
    reason: str
    status: str
    note: str | None


@dataclass(frozen=True, slots=True)
class RequestPlan:
    period_start: date
    period_end_exclusive: date
    canary: date
    requested: tuple[date, ...]
    not_requested: tuple[NotRequested, ...]


def plan_requests(
    start: date, end_exclusive: date, calendar: MoexCalendar, *, canary: date
) -> RequestPlan:
    """Deterministic plan: the canary first, then the other regular weekdays chronologically;
    every other date is listed in ``not_requested`` with its calendar reason."""
    if end_exclusive <= start:
        raise ValueError("plan requires start < end_exclusive")
    regular: list[date] = []
    skipped: list[NotRequested] = []
    day = start
    while day < end_exclusive:
        status = calendar.day_status(day)  # CalendarError outside the coverage
        if status == "regular":
            regular.append(day)
        else:
            note: str | None = None
            fact = calendar.weekend_fact(day) if status == "weekend" else None
            exception = calendar.exception_for(day) if status != "weekend" else None
            if fact is not None:
                note = f"{fact.status} ({fact.source_url})"
            elif exception is not None:
                note = f"{exception.note} ({exception.source_url})"
            skipped.append(NotRequested(day, NOT_REQUESTED_REASONS[status], status, note))
        day += timedelta(days=1)
    if canary not in regular:
        raise ValueError("canary must be a regular weekday of the planned period")
    sequence = (canary, *(d for d in regular if d != canary))
    return RequestPlan(start, end_exclusive, canary, sequence, tuple(skipped))


def plan_for(loaded: LoadedTemplate) -> RequestPlan:
    template = loaded.template
    return plan_requests(
        template.period_start,
        template.period_end_exclusive,
        loaded.calendar,
        canary=template.canary_date,
    )


def plan_document(loaded: LoadedTemplate, plan: RequestPlan) -> dict[str, object]:
    """Machine-readable, deterministic description of the plan (no clock, no network)."""
    requests: list[dict[str, object]] = []
    for index, day in enumerate(plan.requested):
        config = build_day_config(loaded, day)
        requests.append(
            {
                "position": index,
                "date": day.isoformat(),
                "role": "canary" if day == plan.canary else "period",
                "url": config.url,
                "raw_relative": raw_relative(config),
                "regime_id": loaded.calendar.regime_for(day).regime_id,
                "config_sha256": config_sha256(config),
                "sessions": [
                    {
                        "name": window.name,
                        "continuous": window.continuous,
                        "trading_date": window.trading_date.isoformat(),
                        "start_utc": iso_utc(window.start_utc_ns),
                        "end_utc": iso_utc(window.end_utc_ns),
                        "start_utc_ns": window.start_utc_ns,
                        "end_utc_ns": window.end_utc_ns,
                    }
                    for window in config.sessions
                ],
            }
        )
    reasons: dict[str, int] = {}
    for entry in plan.not_requested:
        reasons[entry.reason] = reasons.get(entry.reason, 0) + 1
    return {
        "plan_version": PLAN_VERSION,
        "template_id": loaded.template.template_id,
        "template_sha256": loaded.template_sha256,
        "calendar_id": loaded.calendar.calendar_id,
        "calendar_sha256": loaded.calendar_sha256,
        "period_start": plan.period_start.isoformat(),
        "period_end_exclusive": plan.period_end_exclusive.isoformat(),
        "canary": plan.canary.isoformat(),
        "requested_count": len(plan.requested),
        "requested": requests,
        "not_requested_count": len(plan.not_requested),
        "not_requested_by_reason": dict(sorted(reasons.items())),
        "not_requested": [
            {
                "date": entry.day.isoformat(),
                "reason": entry.reason,
                "calendar_status": entry.status,
                "note": entry.note,
            }
            for entry in plan.not_requested
        ],
    }
