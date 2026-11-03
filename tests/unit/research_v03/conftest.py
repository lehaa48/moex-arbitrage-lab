"""Synthetic fixtures for the H-0001 v0.3 multi-scale tests (workstream B).

Everything here is SYNTHETIC and generated in memory from fixed seeds: log prices, ages,
segments and CSV payloads are invented and are not market data. The parent conftest refuses
every socket. Files with a market-data suffix written below pytest's basetemp are removed at
teardown (function-scoped ``tmp_path`` only), so the repository hygiene scan never sees
them.

Helpers are exposed as fixtures (``--import-mode=importlib``: test modules never import each
other or this file).
"""

import math
import random
import stat
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from moex_arbitrage_lab.research.seconds_analysis import AnalysisDay, VenueDayStates
from moex_arbitrage_lab.research.seconds_series import CommonSegment, Segment
from moex_arbitrage_lab.settings import PROJECT_NAME, ProjectPaths
from moex_arbitrage_lab.settings.storage_paths import _resolve_project_paths

REPO_ROOT = Path(__file__).resolve().parents[3]
V03_CONFIG = REPO_ROOT / "config" / "experiments" / "h0001_multiscale_v0_3.toml"
V02_CONFIG = REPO_ROOT / "config" / "experiments" / "h0001_seconds_screen_v0_2.toml"
MARKET_SUFFIXES = (".csv", ".csv.gz", ".parquet", ".dbn", ".zst", ".feather", ".arrow")
COMEX_HEADER = "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol"
SYNTHETIC_INSTRUMENT_ID = 4242  # synthetic, not a real Databento instrument id
NS = 1_000_000_000
FRESH_AGE_NS = 400_000_000  # synthetic state age (0.4 s)
DAY_START_OFFSET_S = 7 * 3600  # 07:00 UTC

# Weekdays of the synthetic months (2026-09-07 and 2026-10-12 are excluded by the config).
SEPTEMBER = [date(2026, 9, d) for d in (1, 2, 3, 4, 8, 9, 10, 11, 14, 15, 16, 17, 18, 21)]
OCTOBER = [date(2026, 10, d) for d in (1, 2, 5, 6, 7, 8, 9, 13, 14, 15, 16, 19, 20, 21, 22, 23)]


def remove_market_files(root: Path) -> None:
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_file() and path.name.lower().endswith(MARKET_SUFFIXES):
            path.chmod(stat.S_IWRITE | stat.S_IREAD)
            path.unlink()


@pytest.fixture(autouse=True)
def _remove_synthetic_csv(tmp_path: Path) -> Iterator[None]:
    yield
    remove_market_files(tmp_path)


def midnight_s(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())


# --- hand-built analysis days ------------------------------------------------------------------

States = dict[int, tuple[float, int]]


def make_day(
    trading_date: date,
    segments: Sequence[tuple[int, int]],
    comex: States,
    moex: States,
    *,
    quantum_ns: int | None = 1_000_000,
    reconciled: bool = True,
) -> AnalysisDay:
    """``segments`` are (start, end) offsets in seconds from trading-date 00:00 UTC; states are
    keyed by absolute UTC seconds: second -> (log close, age ns)."""
    base = midnight_s(trading_date)
    common = tuple(
        CommonSegment(
            name=f"seg-{index}",
            start_s=base + start,
            end_s=base + end,
            trading_date=trading_date,
            moex_session=f"moex-{index}",
            comex_session=f"comex-{index}",
        )
        for index, (start, end) in enumerate(segments)
    )
    return AnalysisDay(
        trading_date=trading_date,
        common=common,
        moex=VenueDayStates(dict(moex), frozenset(moex)),
        comex=VenueDayStates(dict(comex), frozenset(comex)),
        moex_quantum_ns=quantum_ns,
        reconciled=reconciled,
    )


@pytest.fixture(scope="session")
def day_factory() -> Callable[..., AnalysisDay]:
    return make_day


@pytest.fixture(scope="session")
def midnight() -> Callable[[date], int]:
    return midnight_s


def synthetic_month(
    days: Sequence[date],
    *,
    kind: str,
    seed: int,
    length_s: int = 3600,
    delay_s: int = 5,
    vol: float = 0.0002,
    noise: float = 0.00002,
) -> list[AnalysisDay]:
    """One common segment [07:00, 07:00 + length_s) per day, fresh states every second.

    - ``lead``: COMEX log price a(t); MOEX a(t - delay) + noise, so MOEX bar u+1 copies COMEX
      bar u at width ``delay`` (COMEX leads);
    - ``independent``: COMEX a(t), MOEX b(t), independent walks (no dependence);
    - ``both``: COMEX a(t) + b(t - delay), MOEX a(t - delay) + b(t): equally strong forward and
      reverse next-bar dependence.
    """
    rng = random.Random(seed)  # noqa: S311 - synthetic data
    out: list[AnalysisDay] = []
    for day in days:
        start = midnight_s(day) + DAY_START_OFFSET_S
        first, last = start - 3 * delay_s - 5, start + length_s
        a: dict[int, float] = {}
        b: dict[int, float] = {}
        level_a = level_b = math.log(2400.0)
        for t in range(first, last):
            level_a += rng.gauss(0.0, vol)
            level_b += rng.gauss(0.0, vol)
            a[t], b[t] = level_a, level_b
        comex: States = {}
        moex: States = {}
        for t in range(start, last):
            if kind == "lead":
                c, m = a[t], a[t - delay_s] + rng.gauss(0.0, noise)
            elif kind == "independent":
                c, m = a[t], b[t]
            elif kind == "both":
                c, m = a[t] + b[t - delay_s], a[t - delay_s] + b[t]
            else:
                raise ValueError(kind)
            comex[t] = (c, FRESH_AGE_NS)
            moex[t] = (m, FRESH_AGE_NS)
        out.append(
            make_day(day, [(DAY_START_OFFSET_S, DAY_START_OFFSET_S + length_s)], comex, moex)
        )
    return out


@pytest.fixture(scope="session")
def month_factory() -> Callable[..., list[AnalysisDay]]:
    return synthetic_month


@pytest.fixture(scope="session")
def october() -> list[date]:
    return list(OCTOBER)


# --- synthetic project repository for the CLI workflow -----------------------------------------


def make_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir(exist_ok=True)
    (root / "pyproject.toml").write_text(f'[project]\nname = "{PROJECT_NAME}"\n', encoding="utf-8")
    config_dir = root / "config" / "experiments"
    config_dir.mkdir(parents=True, exist_ok=True)
    # Exact bytes: the v0.2 config SHA-256 pinned by the v0.3 config stays valid.
    (config_dir / V02_CONFIG.name).write_bytes(V02_CONFIG.read_bytes())
    (config_dir / V03_CONFIG.name).write_bytes(V03_CONFIG.read_bytes())
    return root


@pytest.fixture
def synthetic_paths(tmp_path: Path) -> ProjectPaths:
    repo = make_repo(tmp_path / "repo")
    return _resolve_project_paths(repo, repo)


@dataclass
class MarketDay:
    trading_date: date
    segments: tuple[Segment, ...]
    trades: list[tuple[int, Decimal, Decimal]]


@dataclass
class Market:
    days: list[MarketDay]
    comex_csv: bytes


def _price(log_value: float) -> Decimal:
    return Decimal(f"{math.exp(log_value):.6f}")


def generate_market(
    days: Sequence[date], *, lead_s: int = 5, seed: int = 7, seconds: int = 2400
) -> Market:
    """Synthetic trades/ohlcv-1s for the v0.2 seconds builder: common latent walk w; COMEX close
    exp(w_t); MOEX trades in second t at exp(w_{t - lead_s} + noise)."""
    rng = random.Random(seed)  # noqa: S311 - synthetic data
    out_days: list[MarketDay] = []
    bars: list[tuple[int, Decimal]] = []
    for day in days:
        start = midnight_s(day) + DAY_START_OFFSET_S
        walk: dict[int, float] = {}
        level = math.log(2400.0)
        for t in range(start - 120, start + seconds + 120):
            level += rng.gauss(0.0, 0.0002)
            walk[t] = level
        for t in range(start - 60, start + seconds + 60):
            if rng.random() < 0.95:
                bars.append((t, _price(walk[t])))
        trades: list[tuple[int, Decimal, Decimal]] = []
        for t in range(start, start + seconds):
            if rng.random() < 0.95:
                offset = rng.randrange(1, NS)
                value = walk[t - lead_s] + rng.gauss(0.0, 0.00002)
                trades.append((t * NS + offset, _price(value), Decimal(1)))
        segment = Segment("main", start * NS, (start + seconds) * NS, day)
        out_days.append(MarketDay(day, (segment,), trades))
    lines = [COMEX_HEADER]
    for second, close in sorted(bars):
        prices = ",".join([str(close)] * 4)
        lines.append(f"{second * NS},32,1,{SYNTHETIC_INSTRUMENT_ID},{prices},3,GCZ6")
    return Market(out_days, ("\n".join(lines) + "\n").encode("utf-8"))


@pytest.fixture(scope="session")
def market_factory() -> Callable[..., Market]:
    return generate_market
