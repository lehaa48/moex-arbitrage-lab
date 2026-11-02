"""Clock domains and the clock-offset sign convention (ADR-0003).

Adapted (semantics only) from moex-event-alpha ``monitoring/clock.py`` at commit
068bbd8d86a935686d741ea0ef1949617f9f0cab (migration row M-009). The legacy 50/100 ms status
thresholds are discarded: whether a clock is good enough is decided per experiment from the
data, not by a fixed constant.

Sign convention (tested): ``offset_ns = local_utc_ns - reference_utc_ns``. A positive offset
means the local clock is ahead, and ``reference = local - offset``. A sign error would move
times earlier, which is look-ahead leakage.

UTC wall-clock and monotonic readings are different domains: they are never subtracted from
or compared with each other.
"""

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

NS_PER_MS: Final = 1_000_000


class ClockDomain(StrEnum):
    UTC_WALL = "utc_wall"
    MONOTONIC = "monotonic"


class ClockDomainError(ValueError):
    """Readings from different clock domains were combined."""


@dataclass(frozen=True, slots=True)
class ClockReading:
    domain: ClockDomain
    ns: int


def elapsed_ns(start: ClockReading, end: ClockReading) -> int:
    """``end - start`` for two readings of the same clock domain."""
    if start.domain is not end.domain:
        raise ClockDomainError(f"cannot subtract {start.domain} from {end.domain} readings")
    return end.ns - start.ns


class WallClock(Protocol):
    def now_utc_ns(self) -> int: ...


class MonotonicClock(Protocol):
    def now_ns(self) -> int: ...


class SystemWallClock:
    def now_utc_ns(self) -> int:
        return time.time_ns()


class SystemMonotonicClock:
    def now_ns(self) -> int:
        return time.monotonic_ns()


@dataclass(frozen=True, slots=True)
class ClockSample:
    """One comparison of the local wall clock with a reference clock."""

    local_utc_ns: int
    reference_utc_ns: int
    round_trip_ns: int
    reference_source: str

    @property
    def offset_ns(self) -> int:
        return self.local_utc_ns - self.reference_utc_ns


def offset_ms(offset_ns: int) -> int:
    """Milliseconds, rounded half away from zero, in integer arithmetic."""
    magnitude = (abs(offset_ns) + NS_PER_MS // 2) // NS_PER_MS
    return magnitude if offset_ns >= 0 else -magnitude


def reference_time_ns(local_utc_ns: int, offset_ns: int) -> int:
    """Convert a local wall-clock time to reference time: ``local - offset``."""
    return local_utc_ns - offset_ns
