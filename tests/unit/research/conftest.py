"""Synthetic fixtures for the H-0001 v0.2 seconds-screen tests (research workstream).

Everything is SYNTHETIC and generated in memory from a fixed seed: prices, timestamps,
instrument ids and CSV payloads are invented and are not market data. The parent conftest
refuses every socket. Files with a market-data suffix written below ``tmp_path`` are removed at
teardown so that the repository-hygiene scan never sees them.

Helpers are exposed as fixtures (``--import-mode=importlib``: test modules never import each
other or this file).
"""

import math
import random
import stat
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from moex_arbitrage_lab.research.seconds_analysis import (
    AnalysisDay,
    venue_states_from_records,
)
from moex_arbitrage_lab.research.seconds_config import (
    ScreenConfig,
    load_screen_config,
    screen_config_from_mapping,
)
from moex_arbitrage_lab.research.seconds_series import (
    Segment,
    build_comex_series,
    build_moex_day,
    comex_open_intervals,
    common_segments,
    read_comex_ohlcv_csv,
)
from moex_arbitrage_lab.settings import PROJECT_NAME, ProjectPaths
from moex_arbitrage_lab.settings.storage_paths import _resolve_project_paths

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "config" / "experiments" / "h0001_seconds_screen_v0_2.toml"
MARKET_SUFFIXES = (".csv", ".csv.gz", ".parquet", ".dbn", ".zst", ".feather", ".arrow")
COMEX_HEADER = "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol"
SYNTHETIC_INSTRUMENT_ID = 4242  # synthetic, not a real Databento instrument id
NS = 1_000_000_000


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


def make_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir(exist_ok=True)
    (root / "pyproject.toml").write_text(f'[project]\nname = "{PROJECT_NAME}"\n', encoding="utf-8")
    config_dir = root / "config" / "experiments"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / CONFIG_PATH.name).write_bytes(CONFIG_PATH.read_bytes())
    return root


@pytest.fixture
def synthetic_paths(tmp_path: Path) -> ProjectPaths:
    repo = make_repo(tmp_path / "repo")
    return _resolve_project_paths(repo, repo)


@pytest.fixture(scope="session")
def frozen() -> tuple[ScreenConfig, str]:
    return load_screen_config(CONFIG_PATH)


def raw_config_mapping() -> dict[str, object]:
    import tomllib

    return tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def config_mapping() -> Callable[[], dict[str, object]]:
    return raw_config_mapping


@pytest.fixture(scope="session")
def config_from() -> Callable[[dict[str, object]], ScreenConfig]:
    return screen_config_from_mapping


def utc_s(day: date, hour: int, minute: int = 0, second: int = 0) -> int:
    """Exact UTC epoch seconds (integer arithmetic only)."""
    midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return int(midnight.timestamp()) + hour * 3600 + minute * 60 + second


@pytest.fixture(scope="session")
def at() -> Callable[..., int]:
    return utc_s


@dataclass
class SyntheticDay:
    trading_date: date
    segments: tuple[Segment, ...]
    trades: list[tuple[int, Decimal, Decimal]]


@dataclass
class SyntheticMarket:
    days: list[SyntheticDay]
    comex_csv: bytes


def comex_csv(bars: list[tuple[int, Decimal]], symbol: str = "GCZ6") -> bytes:
    """Synthetic Databento-shaped ohlcv-1s CSV: one row per (second, close)."""
    lines = [COMEX_HEADER]
    for second, close in bars:
        prices = ",".join([str(close)] * 4)
        lines.append(f"{second * NS},32,1,{SYNTHETIC_INSTRUMENT_ID},{prices},3,{symbol}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def price(log_value: float) -> Decimal:
    return Decimal(f"{math.exp(log_value):.6f}")


def generate_market(
    days: list[date],
    *,
    lead_s: int,
    seed: int = 7,
    start_hour: int = 7,
    seconds: int = 4200,
    comex_prob: float = 0.92,
    moex_prob: float = 0.92,
    noise: float = 0.00002,
    vol: float = 0.0002,
) -> SyntheticMarket:
    """Common latent walk w; COMEX close at t = exp(w_t); MOEX trades in second t at price
    exp(w_{t - lead_s} + noise). ``lead_s > 0`` means COMEX leads MOEX by ``lead_s`` seconds."""
    rng = random.Random(seed)  # noqa: S311 - synthetic data
    out_days: list[SyntheticDay] = []
    bars: list[tuple[int, Decimal]] = []
    for day in days:
        start = utc_s(day, start_hour)
        margin = 120
        walk: dict[int, float] = {}
        level = math.log(2400.0)
        for t in range(start - margin, start + seconds + margin):
            level += rng.gauss(0.0, vol)
            walk[t] = level
        for t in range(start - 60, start + seconds + 60):
            if rng.random() < comex_prob:
                bars.append((t, price(walk[t])))
        trades: list[tuple[int, Decimal, Decimal]] = []
        for t in range(start, start + seconds):
            if rng.random() < moex_prob:
                source = walk.get(t - lead_s)
                if source is None:
                    continue
                offset = rng.randrange(1, NS)
                trades.append((t * NS + offset, price(source + rng.gauss(0.0, noise)), Decimal(1)))
        segment = Segment("main", start * NS, (start + seconds) * NS, day)
        out_days.append(SyntheticDay(day, (segment,), trades))
    return SyntheticMarket(out_days, comex_csv(sorted(bars)))


@pytest.fixture(scope="session")
def market() -> Callable[..., SyntheticMarket]:
    return generate_market


@pytest.fixture(scope="session")
def make_comex_csv() -> Callable[..., bytes]:
    return comex_csv


SEPTEMBER_DAYS = [date(2026, 9, d) for d in (1, 2, 3, 4)]
OCTOBER_DAYS = [
    date(2026, 10, d) for d in (1, 2, 5, 6, 7, 8, 9, 13, 14, 15, 16, 19, 20, 21, 22, 23)
]


@pytest.fixture(scope="session")
def screen_days() -> tuple[list[date], list[date]]:
    return SEPTEMBER_DAYS, OCTOBER_DAYS


def make_session_repo(base: Path) -> ProjectPaths:
    repo = make_repo(base)
    return _resolve_project_paths(repo, repo)


# Module scope: synthetic market files are removed when the using module finishes, so the
# repository hygiene scan never sees them during a full-suite run.
@pytest.fixture(scope="module")
def session_repo_factory(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Callable[[str], ProjectPaths]]:
    created: list[Path] = []

    def create(name: str) -> ProjectPaths:
        base = tmp_path_factory.mktemp(name)
        created.append(base)
        return make_session_repo(base / "repo")

    yield create
    for base in created:
        remove_market_files(base)


def market_to_days(market: SyntheticMarket, config: ScreenConfig) -> list[AnalysisDay]:
    """Analysis days built directly from the series functions (no files)."""
    bars = read_comex_ohlcv_csv(market.comex_csv, config.comex)
    intervals = comex_open_intervals(config.comex, config.period)
    commons = {
        day.trading_date: common_segments(day.segments, intervals, config.period)
        for day in market.days
    }
    needed = frozenset(seg.comex_session for common in commons.values() for seg in common)
    comex_results, _, _ = build_comex_series(bars, intervals, config.period, needed)
    comex_by_name = {result.segments[0].name: result for result in comex_results}
    days: list[AnalysisDay] = []
    for day in market.days:
        moex = build_moex_day(day.trading_date, day.trades, day.segments)
        common = commons[day.trading_date]
        comex_records = [
            record
            for name in sorted({seg.comex_session for seg in common})
            for record in comex_by_name[name].records
        ]
        days.append(
            AnalysisDay(
                trading_date=day.trading_date,
                common=common,
                moex=venue_states_from_records(moex.records, common, "moex"),
                comex=venue_states_from_records(comex_records, common, "comex"),
                moex_quantum_ns=moex.timestamps.quantum_ns,
                reconciled=True,
            )
        )
    return days


@pytest.fixture(scope="session")
def to_days() -> Callable[[SyntheticMarket, ScreenConfig], list[AnalysisDay]]:
    return market_to_days
