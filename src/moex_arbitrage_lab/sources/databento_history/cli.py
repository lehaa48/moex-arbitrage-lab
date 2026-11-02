"""``python -m moex_arbitrage_lab.sources.databento_history {plan,step-a,step-b} ...``.

Every command first resolves the research runtime (trading disabled) and the project paths.
``plan`` is offline. ``step-a`` and ``step-b`` refuse to run without ``--confirm-network``; a
missing ``DATABENTO_API_KEY`` exits 3 with "DATABENTO_API_KEY not set" before anything is sent
(a verified idempotent ``step-b`` re-run needs neither key nor network). No option can change the
frozen request. Exit codes: 0 ok, 2 usage/refused, 3 blocked/stopped. No key value is printed.
"""

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
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
from moex_arbitrage_lab.sources.databento_history.client import (
    DatabentoError,
    MissingApiKeyError,
    Opener,
)
from moex_arbitrage_lab.sources.databento_history.ledger import LedgerError, ledger_path
from moex_arbitrage_lab.sources.databento_history.request import (
    API_KEY_ENV_NAME,
    BASE_URL,
    CANONICAL_CONTRACT,
    ENDPOINT_METHOD,
    LICENSE_URL,
    METERED_ENDPOINTS,
    RAW_PREFIX,
    REQUEST_SHA256,
    STEP_A_ENDPOINTS,
    STEP_B_ENDPOINTS,
    Endpoint,
    endpoint_params,
    frozen_request_document,
)
from moex_arbitrage_lab.sources.databento_history.step_a import (
    APPROVAL_VERSION,
    CREDIT_UNKNOWN,
    StepAError,
    run_step_a,
)
from moex_arbitrage_lab.sources.databento_history.step_b import (
    DEFAULT_POLL_DELAYS_S,
    STEP_B_STOP_REASONS,
    StepBRefusedError,
    run_step_b,
)
from moex_arbitrage_lab.sources.databento_history.validate import (
    OHLCV_1S_SEMANTICS,
    PayloadRejectedError,
)

EXIT_OK: Final = 0
EXIT_USAGE: Final = 2
EXIT_BLOCKED: Final = 3
NETWORK_COMMANDS: Final = frozenset({"step-a", "step-b"})


def _emit(document: object) -> None:
    sys.stdout.write(json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True) + "\n")


def _fail(code: int, message: str) -> int:
    sys.stderr.write(message + "\n")
    return code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m moex_arbitrage_lab.sources.databento_history",
        description="Databento GLBX.MDP3 ohlcv-1s GCZ6 batch (frozen request, cost-gated).",
    )
    parser.add_argument("command", choices=("plan", "step-a", "step-b"))
    parser.add_argument("--confirm-network", action="store_true")
    parser.add_argument("--run-id", default=None, help="step-a report run id")
    parser.add_argument("--approval", type=Path, default=None, help="user approval JSON")
    parser.add_argument("--step-a-report", type=Path, default=None)
    parser.add_argument(
        "--allow-redownload-from-existing-job",
        action="store_true",
        help="re-download a file of the already created job after a failed attempt",
    )
    parser.add_argument("--operator", default="claude-code")
    return parser


def _endpoint_doc(endpoint: Endpoint) -> dict[str, object]:
    params: dict[str, str] | str
    if endpoint is Endpoint.DOWNLOAD_BATCH_FILE:
        params = "URL from batch.list_files (host/path checked)"
    elif endpoint is Endpoint.LIST_BATCH_FILES:
        params = {"job_id": "<provider job id>"}
    else:
        params = endpoint_params(endpoint)
    return {
        "endpoint": endpoint.value,
        "method": ENDPOINT_METHOD[endpoint],
        "url": BASE_URL + endpoint.value if endpoint is not Endpoint.DOWNLOAD_BATCH_FILE else None,
        "params": params,
        "metered": endpoint in METERED_ENDPOINTS,
    }


def plan_document(paths: ProjectPaths) -> dict[str, object]:
    return {
        "command": "plan",
        "network": False,
        "request": frozen_request_document(),
        "request_sha256": REQUEST_SHA256,
        "credential": {"env_name": API_KEY_ENV_NAME, "auth": "HTTP Basic, key as user name"},
        "step_a": [_endpoint_doc(e) for e in sorted(STEP_A_ENDPOINTS)],
        "step_b": [_endpoint_doc(e) for e in sorted(STEP_B_ENDPOINTS)],
        "ledger_path": paths.relative_to_root(ledger_path(paths)),
        "raw_prefix": f"data/raw/{RAW_PREFIX}",
        "report_dir": paths.relative_to_root(
            paths.resolve(StorageCategory.REPORTS, "databento/step-a")
        ),
        "approval_version": APPROVAL_VERSION,
        "credit": CREDIT_UNKNOWN,
        "decision_rule": "step-a: AWAITING_USER_APPROVAL when coverage/symbology/cache checks "
        "pass (the estimate is recorded exactly, never a stop reason), else STOP_USER_DECISION; "
        "step-b: user approval of the exact estimate, credit statement when > 0, "
        "max_cash_charge_usd 0, get_cost re-check equal to the approved estimate",
        "explicit_user_approval_required": True,
        "poll_delays_s": list(DEFAULT_POLL_DELAYS_S),
        "step_b_stop_reasons": list(STEP_B_STOP_REASONS),
        "canonical_contract": CANONICAL_CONTRACT,
        "license_url": LICENSE_URL,
        "limitations": [OHLCV_1S_SEMANTICS],
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    paths: ProjectPaths | None = None,
    opener: Opener | None = None,
    env: Mapping[str, str] | None = None,
    clock: Callable[[], int] = time.time_ns,
    sleep: Callable[[float], None] = time.sleep,
    poll_delays_s: Sequence[float] = DEFAULT_POLL_DELAYS_S,
) -> int:
    arguments = _parser().parse_args(argv)
    environment: Mapping[str, str] = os.environ if env is None else env
    try:
        load_runtime_safety(environment)
        project = paths if paths is not None else resolve_project_paths()
    except (OrderCapabilityError, TradingModeError, StoragePathError) as error:
        return _fail(EXIT_BLOCKED, f"BLOCKED: {error}")

    command: str = arguments.command
    if command in NETWORK_COMMANDS and not arguments.confirm_network:
        return _fail(EXIT_USAGE, f"refused: {command} contacts the network; pass --confirm-network")
    if command == "step-b" and (arguments.approval is None or arguments.step_a_report is None):
        return _fail(EXIT_USAGE, "refused: step-b requires --approval and --step-a-report")

    try:
        if command == "plan":
            _emit(plan_document(project))
        elif command == "step-a":
            outcome = run_step_a(
                project,
                opener=opener,
                env=environment,
                clock=clock,
                run_id=arguments.run_id,
                operator=arguments.operator,
            )
            _emit(
                {
                    "command": "step-a",
                    "report_path": project.relative_to_root(outcome.report_path),
                    "report_sha256": outcome.report_sha256,
                    "decision": outcome.report["decision"],
                    "stop_reasons": outcome.report["stop_reasons"],
                    "explicit_user_approval_required": True,
                    "report": outcome.report,
                }
            )
        else:
            result = run_step_b(
                project,
                approval_path=arguments.approval,
                step_a_report_path=arguments.step_a_report,
                opener=opener,
                env=environment,
                clock=clock,
                sleep=sleep,
                poll_delays_s=poll_delays_s,
                allow_redownload=arguments.allow_redownload_from_existing_job,
                operator=arguments.operator,
            )
            _emit(
                {
                    "command": "step-b",
                    "job_id": result.job_id,
                    "job_created_now": result.job_created_now,
                    "network_used": result.network_used,
                    "actual_cost_usd": result.actual_cost_usd,
                    "endpoints_called": list(result.endpoints_called),
                    "files": [
                        {
                            "payload_path": f.payload_path,
                            "bytes": f.payload_bytes,
                            "sha256": f.payload_sha256,
                            "already_published": f.already_published,
                        }
                        for f in result.files
                    ],
                }
            )
    except MissingApiKeyError:
        return _fail(EXIT_BLOCKED, f"BLOCKED: {API_KEY_ENV_NAME} not set")
    except DatabentoError as error:
        return _fail(EXIT_BLOCKED, f"BLOCKED: provider request stopped: {error}")
    except StepBRefusedError as error:
        return _fail(EXIT_BLOCKED, f"BLOCKED: step-b stopped: {error}")
    except (StepAError, PayloadRejectedError, LedgerError) as error:
        return _fail(EXIT_BLOCKED, f"BLOCKED: {type(error).__name__}: {error}")
    except (RawPublicationError, StoragePathError) as error:
        return _fail(EXIT_BLOCKED, f"BLOCKED: {type(error).__name__}: {error}")
    except ValidationError as error:
        return _fail(EXIT_BLOCKED, f"BLOCKED: manifest invalid ({error.error_count()} error(s))")
    except FileExistsError:
        return _fail(EXIT_BLOCKED, "BLOCKED: create-once target already exists (use a new run ID)")
    return EXIT_OK
