"""Trading mode resolution (ADR-0001): this research runtime only knows ``disabled``.

Adapted (minimal) from moex-event-alpha ``settings/trading_mode.py`` at commit
068bbd8d86a935686d741ea0ef1949617f9f0cab (migration row M-007). The legacy ``paper`` and
``live`` modes are not part of the enum at all: there is no code path that could represent
them. An absent or empty ``TRADING_MODE`` means ``disabled``; any other value refuses startup.
Error messages and exception chains never carry the configured value, in case a secret was
pasted there.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Final

TRADING_MODE_ENV: Final = "TRADING_MODE"


class TradingMode(StrEnum):
    DISABLED = "disabled"


# ADR-0001: widening this is a new ADR plus a user-approved stage, never configuration.
AUTHORIZED_TRADING_MODES: Final[frozenset[TradingMode]] = frozenset({TradingMode.DISABLED})
# Recognized only to give a precise refusal; they can never be resolved.
_PERMANENTLY_REFUSED: Final = frozenset({"paper", "live"})


class TradingModeError(RuntimeError):
    """The configured trading mode is refused; the process must not start."""


class UnknownTradingModeError(TradingModeError):
    pass


class TradingModeNotAuthorizedError(TradingModeError):
    pass


def resolve_trading_mode(env: Mapping[str, str]) -> TradingMode:
    """Return ``TradingMode.DISABLED`` or raise; nothing else can be configured."""
    raw = env.get(TRADING_MODE_ENV)
    if raw is None or raw == "" or raw == TradingMode.DISABLED.value:
        return TradingMode.DISABLED
    # The raw value is compared, never formatted into the message or kept in a chain.
    if raw in _PERMANENTLY_REFUSED:
        raise TradingModeNotAuthorizedError(
            f"{TRADING_MODE_ENV} requests a trading mode; this project is research-only "
            "(ADR-0001) and accepts only 'disabled'"
        )
    raise UnknownTradingModeError(
        f"{TRADING_MODE_ENV} has an unrecognized value; the only accepted value is 'disabled'"
    )
