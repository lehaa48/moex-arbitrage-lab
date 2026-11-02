"""Clock-offset sign and clock-domain separation (ADR-0003).

Sign scenarios adapted from moex-event-alpha ``tests/unit/test_monitoring.py`` at commit
068bbd8d86a935686d741ea0ef1949617f9f0cab (migration row M-019); the legacy 50/100 ms status
thresholds are intentionally not ported.
"""

import pytest

from moex_arbitrage_lab.contracts import (
    ClockDomain,
    ClockDomainError,
    ClockReading,
    ClockSample,
    SystemMonotonicClock,
    SystemWallClock,
    elapsed_ns,
    offset_ms,
    reference_time_ns,
)
from moex_arbitrage_lab.contracts import clock as clock_module

MS = 1_000_000
REFERENCE = 1_796_000_000_000_000_000


def test_local_clock_ahead_gives_a_positive_offset_and_correct_reference_time() -> None:
    sample = ClockSample(REFERENCE + 5 * MS, REFERENCE, 2 * MS, "synthetic-ntp")

    assert sample.offset_ns == 5 * MS
    assert offset_ms(sample.offset_ns) == 5
    assert reference_time_ns(sample.local_utc_ns, sample.offset_ns) == REFERENCE


def test_local_clock_behind_gives_a_negative_offset_and_correct_reference_time() -> None:
    sample = ClockSample(REFERENCE - 7 * MS, REFERENCE, 2 * MS, "synthetic-ntp")

    assert sample.offset_ns == -7 * MS
    # A sign error would put the corrected time 14 ms early (look-ahead leakage).
    assert reference_time_ns(sample.local_utc_ns, sample.offset_ns) == REFERENCE
    assert reference_time_ns(sample.local_utc_ns, -sample.offset_ns) != REFERENCE


@pytest.mark.parametrize(
    ("offset_ns", "expected"),
    [(0, 0), (499_999, 0), (500_000, 1), (1_500_000, 2), (-500_000, -1), (-1_499_999, -1)],
)
def test_offset_ms_rounds_half_away_from_zero(offset_ns: int, expected: int) -> None:
    assert offset_ms(offset_ns) == expected


def test_same_domain_readings_can_be_subtracted() -> None:
    start = ClockReading(ClockDomain.MONOTONIC, 10)
    end = ClockReading(ClockDomain.MONOTONIC, 35)

    assert elapsed_ns(start, end) == 25


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (ClockDomain.UTC_WALL, ClockDomain.MONOTONIC),
        (ClockDomain.MONOTONIC, ClockDomain.UTC_WALL),
    ],
)
def test_wall_and_monotonic_readings_are_never_mixed(
    first: ClockDomain, second: ClockDomain
) -> None:
    with pytest.raises(ClockDomainError):
        elapsed_ns(ClockReading(first, 1), ClockReading(second, 2))


def test_system_clocks_return_integer_nanoseconds() -> None:
    assert isinstance(SystemWallClock().now_utc_ns(), int)
    assert isinstance(SystemMonotonicClock().now_ns(), int)


def test_legacy_fixed_thresholds_are_not_ported() -> None:
    assert not hasattr(clock_module, "WARNING_OFFSET_NS")
    assert not hasattr(clock_module, "STOP_OFFSET_NS")
    assert not hasattr(clock_module, "assess_clock")
