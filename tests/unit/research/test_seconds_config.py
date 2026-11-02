"""Frozen protocol config: exact spec values, identity hash, and refusal of weaker configs."""

import copy
import hashlib
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from moex_arbitrage_lab.research.seconds_config import (
    ConfigError,
    ScreenConfig,
    load_screen_config,
    month_window_ns,
    to_ns,
)

CONFIG = Path(__file__).resolve().parents[3] / "config/experiments/h0001_seconds_screen_v0_2.toml"

# Assembled from parts so that this file does not contain the banned words themselves.
BANNED_WORDS = ("pro" + "fit", "executable " + "arbitrage", "evidence_of_" + "lead_lag")

Mapping = dict[str, object]


def test_frozen_values_match_the_specification(frozen: tuple[ScreenConfig, str]) -> None:
    config, digest = frozen

    assert digest == hashlib.sha256(CONFIG.read_bytes()).hexdigest()
    assert config.period.start_ns == to_ns(config.period.start_utc)
    assert (config.period.discovery_month, config.period.confirmation_month) == (
        "2026-09",
        "2026-10",
    )
    assert config.moex.instrument_id == "GDZ6_SPBFUT"
    assert config.moex.canonical_contract == "MOEX:GD:2026-12-18"
    assert (config.comex.dataset, config.comex.schema_name, config.comex.raw_symbol) == (
        "GLBX.MDP3",
        "ohlcv-1s",
        "GCZ6",
    )
    assert config.comex.stype_in == "raw_symbol"
    assert ",".join(config.comex.expected_csv_header) == (
        "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol"
    )
    assert config.comex.utc_offset_hours == -5
    assert config.analysis.lag_grid_s == (-60, -30, -10, -5, -2, -1, 0, 1, 2, 5, 10, 30, 60)
    assert config.analysis.positive_grid_s == (1, 2, 5, 10, 30, 60)
    assert config.analysis.staleness_caps_s == (1, 5, 10)
    assert config.analysis.primary_cap_s == 5
    assert config.inference.replicates == 2000
    assert config.inference.null_quantile == 0.95
    assert config.inference.circular_shift_s == 3600
    gate = config.data_gate
    assert (gate.min_confirmation_days, gate.min_paired_seconds_per_day) == (15, 3600)
    assert (gate.max_stale_fraction, gate.max_strongest_day_share) == (0.5, 0.35)
    assert config.decision.min_positive_day_share == 0.60
    assert config.decision.min_caps_same_sign == 2
    exclusions = {e.date: e.reason for e in config.day_universe.primary_exclusions}
    assert exclusions == {
        date(2026, 9, 7): "us_labor_day_cme_holiday_schedule",
        date(2026, 10, 12): "us_columbus_day_cme_schedule_unverified_conservative",
    }
    assert config.day_universe.weekend_reason == "weekend_not_primary"


def test_config_file_contains_no_banned_wording() -> None:
    text = CONFIG.read_text(encoding="utf-8").lower()

    for word in BANNED_WORDS:
        assert word not in text


def _set(mapping: Mapping, path: str, value: object) -> Mapping:
    changed = copy.deepcopy(mapping)
    node: dict[str, object] = changed
    *parents, leaf = path.split(".")
    for key in parents:
        child = node[key]
        assert isinstance(child, dict)
        node = child
    node[leaf] = value
    return changed


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("analysis.lag_grid_s", [-30, -10, -5, -2, -1, 0, 1, 2, 5, 10, 30]),
        ("analysis.positive_grid_s", [1, 2, 5, 10, 30]),
        ("analysis.staleness_caps_s", [1, 5, 30]),
        ("analysis.primary_cap_s", 10),
        ("inference.replicates", 1999),
        ("inference.null_quantile", 0.90),
        ("data_gate.min_confirmation_days", 14),
        ("data_gate.min_paired_seconds_per_day", 3599),
        ("data_gate.max_stale_fraction", 0.6),
        ("data_gate.max_strongest_day_share", 0.4),
        ("decision.min_positive_day_share", 0.55),
        ("decision.min_caps_same_sign", 1),
        ("decision.required_cap_s", 1),
        ("decision.max_single_day_share", 0.5),
        ("period.discovery_month", "2026-10"),
        ("comex.raw_symbol", "GCG7"),
        ("comex.pretty_ts", True),
        ("unexpected_key", 1),
    ],
)
def test_weaker_or_malformed_config_is_refused(
    config_mapping: Callable[[], Mapping],
    config_from: Callable[[Mapping], ScreenConfig],
    path: str,
    value: object,
) -> None:
    with pytest.raises(ConfigError):
        config_from(_set(config_mapping(), path, value))


def test_stricter_values_are_accepted(
    config_mapping: Callable[[], Mapping], config_from: Callable[[Mapping], ScreenConfig]
) -> None:
    stricter = _set(config_mapping(), "inference.replicates", 4000)

    assert config_from(stricter).inference.replicates == 4000


def test_invalid_toml_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("config_version = [", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_screen_config(bad)


def test_month_window_is_half_open() -> None:
    start, end = month_window_ns("2026-12")

    assert end - start == 31 * 86_400 * 1_000_000_000


def test_research_sources_avoid_banned_names_and_words() -> None:
    root = CONFIG.parents[2]
    files = [
        *sorted((root / "src/moex_arbitrage_lab/research").glob("*.py")),
        *sorted((root / "tests/unit/research").glob("*.py")),
        CONFIG,
    ]
    # Assembled from parts so that this file does not match itself.
    names: tuple[str, ...] = (
        "or" + "der",
        "bro" + "ker",
        "exec" + "ution",
        "place" + "_",
        "sub" + "mit",
    )
    names += ("sand" + "box",)
    offenders = [
        f"{path.name}:{word}"
        for path in files
        for word in (*names, *BANNED_WORDS)
        if word in path.read_text(encoding="utf-8").lower()
    ]

    assert offenders == []
