"""``python -m moex_arbitrage_lab.sources.tbank_history COMMAND ...``.

Single-day commands (``--config PATH``): ``plan`` and ``qa`` (offline), ``probe`` (one HEAD)
and ``acquire`` (one GET).

Period commands (``--template PATH``, H-0001 v0.2 seconds screen): ``plan-period`` (offline
JSON plan), ``acquire-period`` (sequential, rate-limited GETs; writes a create-once
``acquisition.json`` report) and ``qa-period`` (offline; writes ``period_qa.json``; optional
``--acquisition-report`` labels HTTP-404 dates as ``missing_file``).

Every command first resolves the research runtime (trading disabled) and the project paths.
Network commands refuse to run without ``--confirm-network``. Exit codes: 0 ok, 2 usage/
refused, 3 blocked/rejected/stopped. No secret is read or printed.
"""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from moex_arbitrage_lab.settings.raw_publication import RawPublicationError
from moex_arbitrage_lab.settings.runtime import OrderCapabilityError, load_runtime_safety
from moex_arbitrage_lab.settings.storage_paths import (
    ProjectPaths,
    StorageCategory,
    StoragePathError,
    resolve_project_paths,
)
from moex_arbitrage_lab.settings.trading_mode import TradingModeError
from moex_arbitrage_lab.sources.tbank_history.acquire import (
    acquire,
    config_sha256,
    raw_relative,
    run_qa,
)
from moex_arbitrage_lab.sources.tbank_history.archive import (
    ARCHIVE_REJECTION_REASONS,
    ArchiveRejectedError,
)
from moex_arbitrage_lab.sources.tbank_history.config import TbankHistoryConfig, load_config
from moex_arbitrage_lab.sources.tbank_history.fetch import (
    FETCH_REJECTION_REASONS,
    REQUEST_HEADERS,
    FetchRejectedError,
    RequestLedger,
    fetch,
    ledger_path,
)
from moex_arbitrage_lab.sources.tbank_history.identity import IdentityRejectedError
from moex_arbitrage_lab.sources.tbank_history.period import (
    LoadedTemplate,
    load_template,
    plan_document,
    plan_for,
)
from moex_arbitrage_lab.sources.tbank_history.period_acquire import PeriodReport, acquire_period
from moex_arbitrage_lab.sources.tbank_history.period_qa import qa_period

EXIT_OK: Final = 0
EXIT_USAGE: Final = 2
EXIT_BLOCKED: Final = 3
NETWORK_COMMANDS: Final = frozenset({"probe", "acquire", "acquire-period"})
DAY_COMMANDS: Final = ("plan", "probe", "acquire", "qa")
PERIOD_COMMANDS: Final = ("plan-period", "acquire-period", "qa-period")
RUN_ID_COMMANDS: Final = frozenset({"qa", "acquire-period", "qa-period"})
PERIOD_EXPERIMENT_ID: Final = "h0001-v0-2-tbank-gdz6"


def _emit(document: object) -> None:
    sys.stdout.write(json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True) + "\n")


def _fail(code: int, message: str) -> int:
    sys.stderr.write(message + "\n")
    return code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m moex_arbitrage_lab.sources.tbank_history",
        description="T-Bank historical trades adapter (research-only, zero-cost).",
    )
    parser.add_argument("command", choices=(*DAY_COMMANDS, *PERIOD_COMMANDS))
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--template", type=Path, default=None)
    parser.add_argument("--acquisition-report", type=Path, default=None)
    parser.add_argument("--confirm-network", action="store_true")
    parser.add_argument("--run-id", default=None)
    return parser


def _write_once(path: Path, document: object) -> None:
    data = (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _new_run_dir(paths: ProjectPaths, run_id: str) -> Path:
    """A fresh run directory; an existing one is never reused (``FileExistsError``)."""
    run_dir = paths.resolve(StorageCategory.REPORTS, f"{PERIOD_EXPERIMENT_ID}/{run_id}")
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(exist_ok=False)
    return run_dir


def _period(paths: ProjectPaths, arguments: argparse.Namespace, loaded: LoadedTemplate) -> int:
    command: str = arguments.command
    plan = plan_for(loaded)
    if command == "plan-period":
        _emit({"command": command, "network": False, **plan_document(loaded, plan)})
        return EXIT_OK
    if command == "acquire-period":
        # The run directory is claimed before any request, so evidence can always be written.
        run_dir = _new_run_dir(paths, arguments.run_id)
        report = acquire_period(paths, loaded, plan)
        document = report.model_dump(mode="json")
        _write_once(run_dir / "acquisition.json", document)
        _emit({"command": command, "report_dir": paths.relative_to_root(run_dir), **document})
        return EXIT_OK if report.status == "complete" else EXIT_BLOCKED
    acquisition = None
    if arguments.acquisition_report is not None:
        acquisition = PeriodReport.model_validate_json(arguments.acquisition_report.read_bytes())
    run_dir = _new_run_dir(paths, arguments.run_id)
    document = qa_period(paths, loaded, plan, acquisition)
    _write_once(run_dir / "period_qa.json", document)
    _emit(
        {
            "command": command,
            "report_dir": paths.relative_to_root(run_dir),
            "instrument_uid": document["instrument_uid"],
            "uid_status": document["uid_status"],
            "dates": document["dates"],
            "totals": document["totals"],
        }
    )
    return EXIT_OK


def _plan(paths: ProjectPaths, config: TbankHistoryConfig) -> dict[str, object]:
    relative = raw_relative(config)
    return {
        "command": "plan",
        "network": False,
        "url": config.url,
        "methods": ["HEAD (probe)", "GET (acquire)"],
        "request_headers": dict(REQUEST_HEADERS),
        "raw_relative": relative,
        "raw_payload_path": f"data/raw/{relative}",
        "payload_format": config.payload_format,
        "expected_content_types": list(config.expected_content_types),
        "min_bytes": config.min_bytes,
        "max_bytes": config.max_bytes,
        "max_uncompressed_bytes": config.max_uncompressed_bytes,
        "request_timeout_s": config.request_timeout_s,
        "requests_per_minute": config.requests_per_minute,
        "min_request_interval_s": config.min_request_interval_s,
        "ledger_path": paths.relative_to_root(ledger_path(paths)),
        "stop_conditions": {
            "fetch": list(FETCH_REJECTION_REASONS),
            "archive": list(ARCHIVE_REJECTION_REASONS),
            "publication": ["recovery_not_clean", "raw_collision", "unverifiable_target"],
        },
        "config_sha256": config_sha256(config),
        "zero_cost_evidence_url": config.zero_cost_evidence_url,
        "license_url": config.license_url,
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        load_runtime_safety(os.environ)
        paths = resolve_project_paths()
    except (OrderCapabilityError, TradingModeError, StoragePathError) as error:
        return _fail(EXIT_BLOCKED, f"BLOCKED: {error}")
    command: str = arguments.command
    period = command in PERIOD_COMMANDS
    source: Path | None = arguments.template if period else arguments.config
    if source is None:
        flag = "--template" if period else "--config"
        return _fail(EXIT_USAGE, f"refused: {command} requires {flag}")
    loaded: LoadedTemplate | None = None
    config: TbankHistoryConfig | None = None
    try:
        if period:
            loaded = load_template(source)
        else:
            config = load_config(source)
    except ValidationError as error:
        return _fail(
            EXIT_USAGE, f"refused: invalid config ({error.error_count()} error(s))\n{error}"
        )
    except (OSError, ValueError) as error:
        return _fail(EXIT_USAGE, f"refused: cannot load config: {type(error).__name__}")

    if command in NETWORK_COMMANDS and not arguments.confirm_network:
        return _fail(EXIT_USAGE, f"refused: {command} contacts the network; pass --confirm-network")
    if command in RUN_ID_COMMANDS and not arguments.run_id:
        return _fail(EXIT_USAGE, f"refused: {command} requires --run-id")

    try:
        if loaded is not None:
            return _period(paths, arguments, loaded)
        if config is None:  # pragma: no cover - one of the two is always loaded
            return _fail(EXIT_USAGE, "refused: no config loaded")
        if command == "plan":
            _emit(_plan(paths, config))
        elif command == "probe":
            result = fetch("HEAD", config, ledger=RequestLedger(ledger_path(paths)))
            _emit(
                {
                    "command": "probe",
                    "url": result.url,
                    "method": result.method,
                    "status": result.status,
                    "headers": result.headers,
                    "started_utc_ns": result.started_utc_ns,
                    "completed_utc_ns": result.completed_utc_ns,
                }
            )
        elif command == "acquire":
            outcome = acquire(paths, config)
            _emit(
                {
                    "command": "acquire",
                    "relative": outcome.relative,
                    "already_published": outcome.already_published,
                    "payload_path": outcome.manifest.payload_path,
                    "payload_sha256": outcome.payload_sha256,
                    "payload_bytes": outcome.payload_bytes,
                    "acquisition_id": outcome.manifest.acquisition_id,
                    "response_status": outcome.manifest.response_status,
                    "response_metadata": outcome.manifest.response_metadata,
                    "retrieval_started_utc_ns": outcome.manifest.retrieval_started_utc_ns,
                    "retrieval_completed_utc_ns": outcome.manifest.retrieval_completed_utc_ns,
                }
            )
        else:
            run_dir = run_qa(paths, config, run_id=arguments.run_id)
            run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            _emit({"command": "qa", "report_dir": paths.relative_to_root(run_dir), "run": run})
    except FetchRejectedError as error:
        return _fail(EXIT_BLOCKED, f"BLOCKED: fetch rejected: {error}")
    except ArchiveRejectedError as error:
        return _fail(EXIT_BLOCKED, f"BLOCKED: archive rejected: {error}")
    except (RawPublicationError, StoragePathError) as error:
        return _fail(EXIT_BLOCKED, f"BLOCKED: {type(error).__name__}: {error}")
    except IdentityRejectedError as error:
        return _fail(EXIT_BLOCKED, f"BLOCKED: identity rejected: {error}")
    except FileExistsError:
        return _fail(EXIT_BLOCKED, "BLOCKED: create-once target already exists (use a new run ID)")
    except RuntimeError as error:
        if str(error).startswith("BLOCKED"):
            return _fail(EXIT_BLOCKED, str(error))
        raise
    except ValueError as error:
        # Header mismatch, failed reconciliation or invalid manifest: the file is rejected.
        return _fail(EXIT_BLOCKED, f"BLOCKED: rejected: {type(error).__name__}: {error}")
    return EXIT_OK
