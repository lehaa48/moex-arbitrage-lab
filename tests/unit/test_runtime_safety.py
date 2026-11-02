"""Research runtime (ADR-0001): trading is permanently disabled; no order capability exists.

Failure scenarios adapted from moex-event-alpha ``tests/unit/test_trading_mode.py`` and
``tests/unit/test_runtime_safety.py`` at commit 068bbd8d86a935686d741ea0ef1949617f9f0cab
(migration row M-019); the legacy credential-reference cases are replaced by "credentials are
never read" because P0 loads none.
"""

import ast
import dataclasses
import traceback
from pathlib import Path

import pytest

import moex_arbitrage_lab
from moex_arbitrage_lab.settings import (
    AUTHORIZED_TRADING_MODES,
    TRADING_MODE_ENV,
    OrderCapabilityError,
    TradingMode,
    TradingModeError,
    TradingModeNotAuthorizedError,
    UnknownTradingModeError,
    load_runtime_safety,
    resolve_trading_mode,
)

PACKAGE_DIR = Path(moex_arbitrage_lab.__file__).parent


def test_disabled_is_the_only_mode_that_exists() -> None:
    assert [mode.value for mode in TradingMode] == ["disabled"]
    assert frozenset({TradingMode.DISABLED}) == AUTHORIZED_TRADING_MODES


@pytest.mark.parametrize(
    "env", [{}, {TRADING_MODE_ENV: ""}, {TRADING_MODE_ENV: "disabled"}, {"UNRELATED": "live"}]
)
def test_absent_empty_or_disabled_mode_resolves_to_disabled(env: dict[str, str]) -> None:
    assert resolve_trading_mode(env) is TradingMode.DISABLED


@pytest.mark.parametrize("value", ["paper", "live"])
def test_paper_and_live_are_refused(value: str) -> None:
    with pytest.raises(TradingModeNotAuthorizedError, match="research-only"):
        resolve_trading_mode({TRADING_MODE_ENV: value})


@pytest.mark.parametrize(
    "value", ["LIVE", "Live", " live", "live ", "Disabled", "on", "true", "1", "real", " "]
)
def test_unknown_values_fail_closed(value: str) -> None:
    with pytest.raises(UnknownTradingModeError):
        resolve_trading_mode({TRADING_MODE_ENV: value})


def test_error_does_not_echo_the_configured_value() -> None:
    pasted_secret = "t.synthetic-pasted-token-value"  # noqa: S105 - synthetic, not real

    with pytest.raises(UnknownTradingModeError) as caught:
        resolve_trading_mode({TRADING_MODE_ENV: pasted_secret})

    error = caught.value
    assert pasted_secret not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert pasted_secret not in "".join(traceback.format_exception(error, chain=True))


def test_default_runtime_is_disabled_without_order_capability() -> None:
    safety = load_runtime_safety({})

    assert safety.trading_mode is TradingMode.DISABLED
    assert safety.order_capability is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        safety.order_capability = True  # type: ignore[misc, assignment]


@pytest.mark.parametrize(
    "env",
    [
        {"TRADING_MODE": "live"},
        {"TRADING_MODE": "paper"},
        {"MOEX_ARBITRAGE_ORDERS_ENABLED": "1"},
        {"MOEX_ARBITRAGE_TRADING_MODE": "disabled"},
        {"moex_arbitrage_execution": "on"},
        {"MOEX_ARBITRAGE_BROKER_ACCOUNT": "synthetic"},
        {"MOEX_ARBITRAGE_LIVE": "true"},
    ],
)
def test_any_attempt_to_enable_trading_or_orders_fails(env: dict[str, str]) -> None:
    with pytest.raises((TradingModeError, OrderCapabilityError)) as caught:
        load_runtime_safety(env)

    for value in env.values():
        if len(value) > 3:
            assert value not in str(caught.value)


def test_credentials_in_the_environment_are_never_read() -> None:
    token = "synthetic-token-never-real-0123456789"  # noqa: S105 - synthetic, not real
    env = {"TBANK_INVEST_TOKEN": token, "DATABENTO_API_KEY": token}

    safety = load_runtime_safety(env)

    assert token not in repr(safety)
    assert [field.name for field in dataclasses.fields(safety)] == [
        "trading_mode",
        "order_capability",
    ]


_ORDER_WORDS = ("order", "broker", "execution", "place_", "submit", "sandbox")


def test_package_defines_no_order_or_trading_interface() -> None:
    # Static check over every module: no function, class or module name that offers orders.
    names: list[str] = []
    for source in PACKAGE_DIR.rglob("*.py"):
        names.append(source.stem)
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.append(node.name)
    offending = [
        name
        for name in names
        if any(word in name.lower() for word in _ORDER_WORDS)
        # The refusal itself is allowed: it is the error raised when orders are requested.
        and name not in {"OrderCapabilityError", "_is_order_name"}
    ]
    assert offending == []
