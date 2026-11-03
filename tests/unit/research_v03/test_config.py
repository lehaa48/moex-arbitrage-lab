"""v0.3 frozen config: identity hash of the exact bytes, frozen values, refusal of unknown keys
and of every setting weaker than the registered protocol."""

import hashlib
from datetime import date
from pathlib import Path

import pytest

from moex_arbitrage_lab.research_v03.config import (
    ConfigError,
    MultiscaleConfig,
    load_multiscale_config,
)

CONFIG = Path(__file__).resolve().parents[3] / "config/experiments/h0001_multiscale_v0_3.toml"


def _variant(tmp_path: Path, old: str, new: str) -> Path:
    text = CONFIG.read_bytes().decode("utf-8")
    assert text.count(old) == 1, old
    target = tmp_path / "variant.toml"
    target.write_bytes(text.replace(old, new).encode("utf-8"))
    return target


def test_real_config_loads_with_the_sha256_of_its_exact_bytes() -> None:
    config, digest = load_multiscale_config(CONFIG)

    assert isinstance(config, MultiscaleConfig)
    assert digest == hashlib.sha256(CONFIG.read_bytes()).hexdigest()
    assert tuple(config.bars.widths_s) == (5, 10, 30, 60)
    assert config.bars.staleness_floor_s == 5
    assert (config.bars.forward_lag_bars, config.bars.reverse_lag_bars) == (1, -1)
    assert config.analysis.min_pairs_per_cell == 30
    assert config.analysis.fisher_r_clamp == 0.999999
    assert (config.inference.seed, config.inference.replicates) == (20261001, 2000)
    assert config.inference.familywise_alpha == 0.05
    assert config.data_gate.min_eligible_days == 15
    assert config.data_gate.max_timestamp_quantum_ns == 1_000_000_000
    assert config.decision.min_positive_day_share == 0.60
    assert config.decision.max_strongest_day_share == 0.35
    assert config.decision.reverse_max_ratio == 0.25
    assert (config.period.calibration_month, config.period.confirmation_month) == (
        "2026-09",
        "2026-10",
    )
    assert config.inputs.seconds_run_id == "p3-20261102-v1"
    assert set(config.day_universe.excluded_dates) == {date(2026, 9, 7), date(2026, 10, 12)}


def test_config_is_frozen() -> None:
    config, _ = load_multiscale_config(CONFIG)

    with pytest.raises(ValueError, match="frozen"):
        config.inference.replicates = 1  # type: ignore[misc]


def test_identity_changes_with_any_byte(tmp_path: Path) -> None:
    _, digest = load_multiscale_config(CONFIG)
    target = tmp_path / "copy.toml"
    target.write_bytes(CONFIG.read_bytes() + b"\n")

    _, other = load_multiscale_config(target)

    assert other != digest


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("min_pairs_per_cell = 30\n", "min_pairs_per_cell = 30\nunknown_key = 1\n"),
        ('protocol_version = "v0.3"\n', 'protocol_version = "v0.3"\nextra = "x"\n'),
        ("[day_universe]\n", "[unknown_section]\nvalue = 1\n\n[day_universe]\n"),
    ],
    ids=["unknown-key-in-section", "unknown-top-level-key", "unknown-section"],
)
def test_unknown_keys_are_refused(tmp_path: Path, old: str, new: str) -> None:
    with pytest.raises(ConfigError):
        load_multiscale_config(_variant(tmp_path, old, new))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("replicates = 2000", "replicates = 1999"),
        ("familywise_alpha = 0.05", "familywise_alpha = 0.051"),
        ("min_eligible_days = 15", "min_eligible_days = 14"),
        ("widths_s = [5, 10, 30, 60]", "widths_s = [5, 7, 30, 60]"),
        ("widths_s = [5, 10, 30, 60]", "widths_s = [5, 10, 30, 45]"),
        ("min_positive_day_share = 0.60", "min_positive_day_share = 0.59"),
        ("max_strongest_day_share = 0.35", "max_strongest_day_share = 0.36"),
        ("reverse_max_ratio = 0.25", "reverse_max_ratio = 0.26"),
    ],
    ids=[
        "replicates-below-2000",
        "alpha-above-0.05",
        "min-eligible-days-below-15",
        "width-7-does-not-divide-60",
        "width-45-does-not-divide-60",
        "looser-positive-day-share",
        "looser-strongest-day-share",
        "looser-reverse-ratio",
    ],
)
def test_weaker_settings_are_refused(tmp_path: Path, old: str, new: str) -> None:
    with pytest.raises(ConfigError):
        load_multiscale_config(_variant(tmp_path, old, new))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("replicates = 2000", "replicates = 5000"),
        ("min_eligible_days = 15", "min_eligible_days = 18"),
        ("min_positive_day_share = 0.60", "min_positive_day_share = 0.70"),
        ("max_strongest_day_share = 0.35", "max_strongest_day_share = 0.30"),
    ],
)
def test_stricter_settings_are_accepted(tmp_path: Path, old: str, new: str) -> None:
    config, _ = load_multiscale_config(_variant(tmp_path, old, new))

    assert isinstance(config, MultiscaleConfig)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('calibration_month = "2026-09"', 'calibration_month = "2026-08"'),
        ('confirmation_month = "2026-10"', 'confirmation_month = "2026-11"'),
        ("widths_s = [5, 10, 30, 60]", "widths_s = [5, 10, 30]"),
        ("widths_s = [5, 10, 30, 60]", "widths_s = [1, 5, 10, 30, 60]"),
        ("widths_s = [5, 10, 30, 60]", "widths_s = [5, 10, 20, 60]"),
        ("familywise_alpha = 0.05", "familywise_alpha = 0.01"),
        ("familywise_alpha = 0.05", "familywise_alpha = 0.04"),
        ("staleness_floor_s = 5", "staleness_floor_s = 4"),
        ("staleness_floor_s = 5", "staleness_floor_s = 1"),
        ("seed = 20261001", "seed = 20261002"),
        ("seed = 20261001", "seed = 20260901"),
        ("excluded_dates = [2026-09-07, 2026-10-12]", "excluded_dates = [2026-09-07]"),
        ("excluded_dates = [2026-09-07, 2026-10-12]", "excluded_dates = [2026-10-12]"),
        (
            "excluded_dates = [2026-09-07, 2026-10-12]",
            "excluded_dates = [2026-09-07, 2026-10-12, 2026-10-13]",
        ),
        ("excluded_dates = [2026-09-07, 2026-10-12]", "excluded_dates = []"),
    ],
    ids=[
        "calibration-month",
        "confirmation-month",
        "widths-subset",
        "widths-superset",
        "widths-other",
        "alpha-stricter",
        "alpha-other",
        "staleness-floor-4",
        "staleness-floor-1",
        "seed-other",
        "seed-v02",
        "excluded-only-september",
        "excluded-only-october",
        "excluded-extra-date",
        "excluded-none",
    ],
)
def test_registered_values_must_be_exact(tmp_path: Path, old: str, new: str) -> None:
    with pytest.raises(ConfigError):
        load_multiscale_config(_variant(tmp_path, old, new))
