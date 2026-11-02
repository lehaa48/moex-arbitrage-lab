"""Secret redaction (docs/STORAGE_AND_SECRETS_POLICY.md): diagnostics never echo a secret."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from moex_arbitrage_lab.settings import (
    KNOWN_SECRET_ENV_NAMES,
    is_secret_name,
    redact,
    redacted_environment,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_TOKEN = "t.SYNTHETIC-fake-token-not-real-0123456789"  # noqa: S105 - obvious fake


def _load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "name",
    [
        "TBANK_INVEST_TOKEN",
        "DATABENTO_API_KEY",
        "Authorization",
        "x-api-key",
        "client_secret",
        "password",
        "Cookie",
        "AWS_PRIVATE_KEY",
    ],
)
def test_secret_names_are_recognized(name: str) -> None:
    assert is_secret_name(name)


@pytest.mark.parametrize("name", ["TEMP", "UV_CACHE_DIR", "symbols", "dataset", "limit"])
def test_ordinary_names_are_not_secret(name: str) -> None:
    assert not is_secret_name(name)


def test_redact_never_returns_any_part_of_the_value() -> None:
    assert redact(FAKE_TOKEN) == "<redacted>"
    assert redact("") == "<empty>"
    assert redact(None) == "<empty>"
    assert FAKE_TOKEN[:4] not in redact(FAKE_TOKEN)


def test_redacted_environment_hides_secret_values_only() -> None:
    env = {"TBANK_INVEST_TOKEN": FAKE_TOKEN, "TEMP": "D:\\x", "OTHER": "y"}

    shown = redacted_environment(env, frozenset({"TBANK_INVEST_TOKEN", "TEMP", "MISSING"}))

    assert shown == {"TBANK_INVEST_TOKEN": "<redacted>", "TEMP": "D:\\x"}


def test_env_example_holds_names_only() -> None:
    lines = (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assignments = dict(line.split("=", 1) for line in lines if line.strip())

    assert set(assignments) >= KNOWN_SECRET_ENV_NAMES
    assert all(value == "" for value in assignments.values())
    assert not (REPO_ROOT / ".env").exists()


def test_storage_report_never_prints_a_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in KNOWN_SECRET_ENV_NAMES:
        monkeypatch.setenv(name, FAKE_TOKEN)
    monkeypatch.setenv("TRADING_MODE", FAKE_TOKEN)
    check_storage_paths = _load_script("check_storage_paths")

    exit_code = check_storage_paths.main()

    output = capsys.readouterr().out
    assert FAKE_TOKEN not in output
    assert "TBANK_INVEST_TOKEN: <redacted>" in output
    # The pasted value in TRADING_MODE refuses the run without being echoed.
    assert exit_code == 1
    assert "TRADING_MODE has an unrecognized value" in output
