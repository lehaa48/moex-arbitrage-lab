"""Frozen protocol config of the H-0001 v0.2 seconds screen.

The TOML file ``config/experiments/h0001_seconds_screen_v0_2.toml`` is the single record of
every pre-outcome choice. ``load_screen_config`` validates it strictly (unknown keys are
refused) and returns it together with the SHA-256 of the exact file bytes; that hash is the
config identity written into every manifest and artifact.

The specification floor is also enforced in code (``SPEC_*`` below): a config that would make
the screen weaker than docs/tasks/CLAUDE_SECONDS_SCREEN_PIVOT_PROMPT.md (another lag grid,
fewer replicates, a looser data gate or decision threshold) is refused, so editing the TOML
alone cannot weaken the protocol.
"""

import hashlib
import tomllib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NS_PER_SECOND: Final = 1_000_000_000

# Specification floor (pivot prompt). The config must equal or be stricter than these.
SPEC_LAG_GRID_S: Final = (-60, -30, -10, -5, -2, -1, 0, 1, 2, 5, 10, 30, 60)
SPEC_POSITIVE_GRID_S: Final = (1, 2, 5, 10, 30, 60)
SPEC_STALENESS_CAPS_S: Final = (1, 5, 10)
SPEC_PRIMARY_CAP_S: Final = 5
SPEC_MIN_REPLICATES: Final = 2000
SPEC_NULL_QUANTILE: Final = 0.95
SPEC_MIN_CONFIRMATION_DAYS: Final = 15
SPEC_MIN_PAIRED_SECONDS: Final = 3600
SPEC_MAX_STALE_FRACTION: Final = 0.5
SPEC_MAX_STRONGEST_DAY_SHARE: Final = 0.35
SPEC_MIN_POSITIVE_DAY_SHARE: Final = 0.60
SPEC_MIN_CAPS_SAME_SIGN: Final = 2

MonthText = Annotated[str, StringConstraints(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
ReasonCode = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_]*$", max_length=96)]
LocalTime = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
Weekday = Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAYS: Final = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class ConfigError(ValueError):
    """The protocol config is malformed or weaker than the specification."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class PeriodConfig(_Frozen):
    start_utc: datetime
    end_utc: datetime
    discovery_month: MonthText
    confirmation_month: MonthText

    @model_validator(mode="after")
    def _check(self) -> Self:
        for value in (self.start_utc, self.end_utc):
            if value.utcoffset() != timedelta(0) or value.microsecond or value.second:
                raise ValueError("period bounds must be whole-minute UTC instants")
        if not self.start_utc < self.end_utc:
            raise ValueError("period start must precede its end")
        if not self.discovery_month < self.confirmation_month:
            raise ValueError("discovery month must precede the confirmation month")
        for month in (self.discovery_month, self.confirmation_month):
            start, end = month_window_ns(month)
            if start < to_ns(self.start_utc) or end > to_ns(self.end_utc):
                raise ValueError(f"month {month} is outside the period")
        return self

    @property
    def start_ns(self) -> int:
        return to_ns(self.start_utc)

    @property
    def end_ns(self) -> int:
        return to_ns(self.end_utc)


class MoexConfig(_Frozen):
    instrument_id: Literal["GDZ6_SPBFUT"]
    canonical_contract: Literal["MOEX:GD:2026-12-18"]


class ComexConfig(_Frozen):
    dataset: Literal["GLBX.MDP3"]
    schema_name: Literal["ohlcv-1s"] = Field(alias="schema")
    stype_in: Literal["raw_symbol"]
    raw_symbol: Literal["GCZ6"]
    encoding: Literal["csv"]
    compression: Literal["none"]
    pretty_px: Literal[True]
    pretty_ts: Literal[False]
    map_symbols: Literal[True]
    expected_csv_header: tuple[str, ...] = Field(min_length=1)
    expected_rtype: int
    utc_offset_hours: int = Field(ge=-12, le=14)
    globex_open_local: LocalTime
    globex_close_local: LocalTime
    trade_weekdays: tuple[Weekday, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        header = self.expected_csv_header
        if len(set(header)) != len(header) or any(
            not name or name.strip() != name for name in header
        ):
            raise ValueError("expected_csv_header must hold unique, unpadded column names")
        required = {"ts_event", "open", "high", "low", "close", "volume", "symbol"}
        required |= {"rtype", "instrument_id", "publisher_id"}
        if not required <= set(header):
            raise ValueError(f"expected_csv_header must contain {sorted(required)}")
        if len(set(self.trade_weekdays)) != len(self.trade_weekdays):
            raise ValueError("trade_weekdays must be unique")
        return self


class AnalysisConfig(_Frozen):
    lag_grid_s: tuple[int, ...]
    positive_grid_s: tuple[int, ...]
    staleness_caps_s: tuple[int, ...]
    primary_cap_s: int
    min_pairs_for_correlation: int = Field(ge=3)
    fisher_r_clamp: float = Field(gt=0.9, lt=1.0)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.lag_grid_s != SPEC_LAG_GRID_S:
            raise ValueError(f"lag_grid_s must be exactly {SPEC_LAG_GRID_S}")
        if self.positive_grid_s != SPEC_POSITIVE_GRID_S:
            raise ValueError(f"positive_grid_s must be exactly {SPEC_POSITIVE_GRID_S}")
        if self.staleness_caps_s != SPEC_STALENESS_CAPS_S:
            raise ValueError(f"staleness_caps_s must be exactly {SPEC_STALENESS_CAPS_S}")
        if self.primary_cap_s != SPEC_PRIMARY_CAP_S:
            raise ValueError(f"primary_cap_s must be {SPEC_PRIMARY_CAP_S}")
        return self


class InferenceConfig(_Frozen):
    seed: int = Field(ge=0)
    replicates: int = Field(ge=SPEC_MIN_REPLICATES)
    null_quantile: float
    null_method: Literal["day_mismatch_derangement"]
    circular_shift_s: int = Field(ge=600)
    impossible_time_method: Literal["next_included_day_same_time_of_day"]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.null_quantile < SPEC_NULL_QUANTILE or self.null_quantile >= 1:
            raise ValueError(f"null_quantile must be in [{SPEC_NULL_QUANTILE}, 1)")
        return self


class DataGateConfig(_Frozen):
    min_confirmation_days: int = Field(ge=SPEC_MIN_CONFIRMATION_DAYS)
    min_paired_seconds_per_day: int = Field(ge=SPEC_MIN_PAIRED_SECONDS)
    max_stale_fraction: float = Field(gt=0, le=SPEC_MAX_STALE_FRACTION)
    max_strongest_day_share: float = Field(gt=0, le=SPEC_MAX_STRONGEST_DAY_SHARE)
    strongest_day_share_definition: Literal["max_signed_day_z_over_abs_sum_z"]
    max_timestamp_quantum_ns: int = Field(gt=0, le=NS_PER_SECOND)
    day_quality_rule: Literal["exclude_day"]


class DecisionConfig(_Frozen):
    min_positive_day_share: float = Field(ge=SPEC_MIN_POSITIVE_DAY_SHARE, lt=1.0)
    min_caps_same_sign: int = Field(ge=SPEC_MIN_CAPS_SAME_SIGN, le=3)
    required_cap_s: int
    max_single_day_share: float = Field(gt=0, le=SPEC_MAX_STRONGEST_DAY_SHARE)
    control_reproduces_rule: Literal["positive_and_at_least_null_threshold"]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.required_cap_s != SPEC_PRIMARY_CAP_S:
            raise ValueError(f"required_cap_s must be {SPEC_PRIMARY_CAP_S}")
        return self


class DayExclusion(_Frozen):
    date: date
    reason: ReasonCode


class DayUniverseConfig(_Frozen):
    weekend_reason: ReasonCode
    primary_exclusions: tuple[DayExclusion, ...]

    @model_validator(mode="after")
    def _check(self) -> Self:
        dates = [item.date for item in self.primary_exclusions]
        if len(set(dates)) != len(dates):
            raise ValueError("primary_exclusions dates must be unique")
        return self


class ScreenConfig(_Frozen):
    config_version: Literal["h0001_seconds_screen_config_v1"]
    hypothesis: Literal["H-0001"]
    protocol_version: Literal["v0.2"]
    period: PeriodConfig
    moex: MoexConfig
    comex: ComexConfig
    analysis: AnalysisConfig
    inference: InferenceConfig
    data_gate: DataGateConfig
    decision: DecisionConfig
    day_universe: DayUniverseConfig

    @model_validator(mode="after")
    def _check(self) -> Self:
        first = self.period.start_utc.date()
        last = (self.period.end_utc - timedelta(microseconds=1)).date()
        for item in self.day_universe.primary_exclusions:
            if not first <= item.date <= last:
                raise ValueError(f"exclusion {item.date} is outside the period")
        if self.decision.max_single_day_share > self.data_gate.max_strongest_day_share:
            raise ValueError("decision single-day share cannot be looser than the data gate")
        return self


def to_ns(value: datetime) -> int:
    """Exact integer UTC nanoseconds of an aware datetime (no float arithmetic)."""
    if value.utcoffset() is None:
        raise ValueError("naive datetime refused")
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * NS_PER_SECOND + delta.microseconds * 1_000


def date_start_ns(day: date) -> int:
    return to_ns(datetime(day.year, day.month, day.day, tzinfo=UTC))


def month_window_ns(month: str) -> tuple[int, int]:
    """Half-open UTC window [first day 00:00, next month first day 00:00) of ``YYYY-MM``."""
    year, number = (int(part) for part in month.split("-"))
    start = datetime(year, number, 1, tzinfo=UTC)
    end = datetime(year + number // 12, number % 12 + 1, 1, tzinfo=UTC)
    return to_ns(start), to_ns(end)


def month_of(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def load_screen_config(path: Path) -> tuple[ScreenConfig, str]:
    """Return the validated config and the SHA-256 hex of the exact file bytes."""
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    return parse_screen_config(data), digest


def parse_screen_config(data: bytes) -> ScreenConfig:
    try:
        raw = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"config is not valid UTF-8 TOML: {error}") from None
    return screen_config_from_mapping(raw)


def screen_config_from_mapping(raw: dict[str, object]) -> ScreenConfig:
    # TOML arrays arrive as lists; strict mode wants tuples for the frozen sequence fields.
    try:
        return ScreenConfig.model_validate(_tuples(raw))
    except ValueError as error:
        raise ConfigError(str(error)) from None


def _tuples(value: object) -> object:
    if isinstance(value, dict):
        return {key: _tuples(item) for key, item in value.items()}
    if isinstance(value, list):
        return tuple(_tuples(item) for item in value)
    return value
