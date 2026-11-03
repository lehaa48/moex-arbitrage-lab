"""Frozen protocol config of the H-0001 v0.3 multi-scale confirmation.

The TOML file ``config/experiments/h0001_multiscale_v0_3.toml`` records every pre-outcome
choice of docs/experiments/H-0001_MULTISCALE_V0_3.md. ``load_multiscale_config`` validates it
strictly (unknown keys refused, strict types, frozen models) and returns it together with the
SHA-256 of the exact file bytes; that hash is the config identity written into every artifact
and into the October opened marker.

Registered values are pinned exactly in code (``SPEC_*``): calibration month 2026-09,
confirmation month 2026-10, bar widths (5, 10, 30, 60) s, staleness floor 5 s, lags +1
(COMEX leads) and -1 (reverse control) bars, seed 20261001, family-wise alpha 0.05 and the
excluded dates 2026-09-07 and 2026-10-12. Floors (stricter allowed): at least 2000
replicates, at least 30 pairs per cell, at least 15 eligible days, and no decision
threshold looser than the registered TOML values. Editing the TOML alone cannot weaken or
re-target the protocol.
"""

import hashlib
import tomllib
from datetime import date
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from moex_arbitrage_lab.research.seconds_config import (
    NS_PER_SECOND,
    ConfigError,
    month_of,
)

# Registered values pinned exactly (docs/experiments/H-0001_MULTISCALE_V0_3.md).
SPEC_CALIBRATION_MONTH: Final = "2026-09"
SPEC_CONFIRMATION_MONTH: Final = "2026-10"
SPEC_WIDTHS_S: Final = (5, 10, 30, 60)
SPEC_STALENESS_FLOOR_S: Final = 5
SPEC_SEED: Final = 20261001
SPEC_FAMILYWISE_ALPHA: Final = 0.05
SPEC_EXCLUDED_DATES: Final = frozenset({date(2026, 9, 7), date(2026, 10, 12)})
# Protocol floor. Stricter values are allowed.
SPEC_FORWARD_LAG_BARS: Final = 1
SPEC_REVERSE_LAG_BARS: Final = -1
SPEC_MIN_PAIRS_PER_CELL: Final = 30
SPEC_MIN_REPLICATES: Final = 2000
SPEC_MIN_ELIGIBLE_DAYS: Final = 15
SPEC_MIN_POSITIVE_DAY_SHARE: Final = 0.60
SPEC_MAX_STRONGEST_DAY_SHARE: Final = 0.35
SPEC_MAX_REVERSE_RATIO: Final = 0.25

Sha256Text = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
RunIdText = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
ConfigRelative = Annotated[
    str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]*(/[a-z0-9][a-z0-9_-]*)*\.toml$")
]

__all__ = [
    "ConfigError",
    "MultiscaleConfig",
    "load_multiscale_config",
    "multiscale_config_from_mapping",
    "parse_multiscale_config",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class InputsConfig(_Frozen):
    seconds_run_id: RunIdText
    seconds_manifest_sha256: Sha256Text
    # Relative to config/ (resolved through research.seconds_store.resolve_config_file).
    v0_2_config_path: ConfigRelative
    v0_2_config_sha256: Sha256Text


class PeriodConfig(_Frozen):
    calibration_month: Literal["2026-09"]
    confirmation_month: Literal["2026-10"]


class BarsConfig(_Frozen):
    widths_s: tuple[int, ...] = Field(min_length=1)
    staleness_floor_s: Literal[5]
    forward_lag_bars: Literal[1]
    reverse_lag_bars: Literal[-1]

    @model_validator(mode="after")
    def _check(self) -> Self:
        widths = self.widths_s
        if any(width <= 0 or 60 % width for width in widths):
            raise ValueError("every bar width must be a positive divisor of 60 s")
        if list(widths) != sorted(set(widths)):
            raise ValueError("bar widths must be unique and ascending")
        if widths != SPEC_WIDTHS_S:
            raise ValueError(f"widths_s must be exactly {SPEC_WIDTHS_S}")
        return self


class AnalysisConfig(_Frozen):
    min_pairs_per_cell: int = Field(ge=SPEC_MIN_PAIRS_PER_CELL)
    fisher_r_clamp: float = Field(gt=0.9, lt=1.0)


class InferenceConfig(_Frozen):
    seed: Literal[20261001]
    replicates: int = Field(ge=SPEC_MIN_REPLICATES)
    null_method: Literal["day_mismatch_derangement"]
    familywise_alpha: float

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.familywise_alpha != SPEC_FAMILYWISE_ALPHA:
            raise ValueError(f"familywise_alpha must be exactly {SPEC_FAMILYWISE_ALPHA}")
        return self


class DataGateConfig(_Frozen):
    min_eligible_days: int = Field(ge=SPEC_MIN_ELIGIBLE_DAYS)
    max_timestamp_quantum_ns: int = Field(gt=0, le=NS_PER_SECOND)


class DecisionConfig(_Frozen):
    min_positive_day_share: float = Field(ge=SPEC_MIN_POSITIVE_DAY_SHARE, lt=1.0)
    max_strongest_day_share: float = Field(gt=0.0, le=SPEC_MAX_STRONGEST_DAY_SHARE)
    reverse_max_ratio: float = Field(ge=0.0, le=SPEC_MAX_REVERSE_RATIO)


class DayUniverseConfig(_Frozen):
    excluded_dates: tuple[date, ...]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if len(set(self.excluded_dates)) != len(self.excluded_dates):
            raise ValueError("excluded_dates must be unique")
        if frozenset(self.excluded_dates) != SPEC_EXCLUDED_DATES:
            raise ValueError("excluded_dates must be exactly 2026-09-07 and 2026-10-12")
        return self


class MultiscaleConfig(_Frozen):
    config_version: Literal["h0001_multiscale_config_v1"]
    hypothesis: Literal["H-0001"]
    protocol_version: Literal["v0.3"]
    inputs: InputsConfig
    period: PeriodConfig
    bars: BarsConfig
    analysis: AnalysisConfig
    inference: InferenceConfig
    data_gate: DataGateConfig
    decision: DecisionConfig
    day_universe: DayUniverseConfig

    @model_validator(mode="after")
    def _check(self) -> Self:
        months = {self.period.calibration_month, self.period.confirmation_month}
        for day in self.day_universe.excluded_dates:
            if month_of(day) not in months:
                raise ValueError(
                    f"excluded date {day} is outside the calibration/confirmation months"
                )
        return self

    @property
    def lags(self) -> tuple[int, int]:
        """(forward, reverse) lags in bars."""
        return (self.bars.forward_lag_bars, self.bars.reverse_lag_bars)

    def excluded_in(self, month: str) -> frozenset[date]:
        return frozenset(day for day in self.day_universe.excluded_dates if month_of(day) == month)


def load_multiscale_config(path: Path) -> tuple[MultiscaleConfig, str]:
    """Return the validated config and the SHA-256 hex of the exact file bytes."""
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    return parse_multiscale_config(data), digest


def parse_multiscale_config(data: bytes) -> MultiscaleConfig:
    try:
        raw = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"config is not valid UTF-8 TOML: {error}") from None
    return multiscale_config_from_mapping(raw)


def multiscale_config_from_mapping(raw: dict[str, object]) -> MultiscaleConfig:
    # TOML arrays arrive as lists; strict mode wants tuples for the frozen sequence fields.
    try:
        return MultiscaleConfig.model_validate(_tuples(raw))
    except ValueError as error:
        raise ConfigError(str(error)) from None


def _tuples(value: object) -> object:
    if isinstance(value, dict):
        return {key: _tuples(item) for key, item in value.items()}
    if isinstance(value, list):
        return tuple(_tuples(item) for item in value)
    return value
