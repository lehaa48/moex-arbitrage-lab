"""CLI: offline plan, --confirm-network gate, missing key exit 3, no key in any output."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from moex_arbitrage_lab.settings import ProjectPaths
from moex_arbitrage_lab.sources.databento_history.cli import main

if TYPE_CHECKING:
    from tests.unit.databento_history.conftest import FakeClock, FakeOpener, FakeProvider, Kit


def test_plan_is_offline_and_describes_the_frozen_request(
    paths: ProjectPaths, capsys: pytest.CaptureFixture[str], kit: "Kit"
) -> None:
    code = main(["plan"], paths=paths, opener=kit.refusing_opener(), env={})

    assert code == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["network"] is False
    assert plan["request"]["symbols"] == "GCZ6"
    assert plan["request"]["schema"] == "ohlcv-1s"
    assert {e["endpoint"] for e in plan["step_a"]} == {
        "metadata.list_schemas",
        "metadata.get_dataset_range",
        "symbology.resolve",
        "metadata.get_record_count",
        "metadata.get_billable_size",
        "metadata.get_cost",
    }
    assert [e["endpoint"] for e in plan["step_b"] if e["metered"]] == ["batch.submit_job"]
    assert plan["explicit_user_approval_required"] is True
    assert plan["ledger_path"] == "logs/databento/requests.jsonl"


@pytest.mark.parametrize("command", ["step-a", "step-b"])
def test_network_commands_need_confirmation(
    paths: ProjectPaths, capsys: pytest.CaptureFixture[str], kit: "Kit", command: str
) -> None:
    code = main([command], paths=paths, opener=kit.refusing_opener(), env={})

    assert code == 2
    assert "--confirm-network" in capsys.readouterr().err


def test_no_cli_option_can_change_the_request(paths: ProjectPaths, kit: "Kit") -> None:
    for option in ("--schema", "--symbols", "--start", "--end", "--dataset", "--encoding"):
        with pytest.raises(SystemExit) as caught:
            main(["plan", option, "x"], paths=paths, opener=kit.refusing_opener(), env={})
        assert caught.value.code == 2


@pytest.mark.parametrize("env", [{}, {"DATABENTO_API_KEY": ""}])
def test_missing_key_exits_3_with_clear_message(
    paths: ProjectPaths,
    capsys: pytest.CaptureFixture[str],
    kit: "Kit",
    env: dict[str, str],
) -> None:
    code = main(["step-a", "--confirm-network"], paths=paths, opener=kit.refusing_opener(), env=env)

    assert code == 3
    assert "DATABENTO_API_KEY not set" in capsys.readouterr().err


def test_step_a_cli_prints_report_without_key(
    paths: ProjectPaths,
    capsys: pytest.CaptureFixture[str],
    opener: "FakeOpener",
    env: dict[str, str],
    clock: "FakeClock",
    kit: "Kit",
) -> None:
    code = main(
        ["step-a", "--confirm-network", "--run-id", "synthetic-cli"],
        paths=paths,
        opener=opener,
        env=env,
        clock=clock,
    )

    captured = capsys.readouterr()
    assert code == 0
    output = json.loads(captured.out)
    assert output["decision"] == "AWAITING_USER_APPROVAL"
    assert output["explicit_user_approval_required"] is True
    assert output["report_path"].endswith("synthetic-cli/step-a-report.json")
    assert kit.fake_key not in captured.out + captured.err


def test_step_b_cli_requires_approval_arguments(
    paths: ProjectPaths, capsys: pytest.CaptureFixture[str], kit: "Kit", env: dict[str, str]
) -> None:
    code = main(["step-b", "--confirm-network"], paths=paths, opener=kit.refusing_opener(), env=env)

    assert code == 2
    assert "--approval" in capsys.readouterr().err


def test_step_b_cli_end_to_end_and_mismatch(
    paths: ProjectPaths,
    capsys: pytest.CaptureFixture[str],
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    kit: "Kit",
    tmp_path: Path,
    approve: Callable[..., Path],
) -> None:
    assert (
        main(
            ["step-a", "--confirm-network", "--run-id", "synthetic-cli"],
            paths=paths,
            opener=opener,
            env=env,
            clock=clock,
        )
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    report_path = paths.root / out["report_path"]
    bad = approve(tmp_path, report_path, out["report"], approved_estimate_usd="0.5")
    args = ["step-b", "--confirm-network", "--approval", str(bad)]
    args += ["--step-a-report", str(report_path)]

    assert main(args, paths=paths, opener=opener, env=env, clock=clock, sleep=lambda s: None) == 3
    assert "estimate_mismatch" in capsys.readouterr().err

    approve(tmp_path, report_path, out["report"])
    code = main(
        args,
        paths=paths,
        opener=opener,
        env=env,
        clock=clock,
        sleep=lambda s: None,
        poll_delays_s=(0.0, 0.0),
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    result = json.loads(captured.out)
    assert result["job_created_now"] is True
    assert [f["payload_path"].rsplit("/", 1)[1] for f in result["files"]] == [
        kit.csv_name,
        kit.aux_name,
    ]
    assert kit.fake_key not in captured.out + captured.err
    assert provider.endpoint_paths().count("batch.submit_job") == 1
