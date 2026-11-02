"""Single startup entry point for safety-relevant runtime settings (ADR-0001).

Adapted (minimal) from moex-event-alpha ``settings/runtime.py`` at commit
068bbd8d86a935686d741ea0ef1949617f9f0cab (migration row M-007). The legacy credential
references are dropped: P0 loads no credentials. The research runtime has no order or
trading capability; configuration names that would try to enable one are refused.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from moex_arbitrage_lab.settings.trading_mode import TradingMode, resolve_trading_mode

# Reserved so that no future configuration can quietly switch on order entry or trading.
_ORDER_CAPABILITY_NAME: Final = re.compile(
    r"^MOEX_ARBITRAGE_(ORDER|ORDERS|TRADING|EXECUTION|BROKER|LIVE|PAPER)(_|$)", re.IGNORECASE
)


class OrderCapabilityError(RuntimeError):
    """Something tried to enable order entry or trading; the process must not start."""


@dataclass(frozen=True, slots=True)
class RuntimeSafety:
    trading_mode: TradingMode
    order_capability: Literal[False] = False


def load_runtime_safety(env: Mapping[str, str]) -> RuntimeSafety:
    """Resolve the research runtime from ``env`` (normally ``os.environ``) or raise.

    Every process entry point calls this before doing any work. Credentials are never read.
    """
    mode = resolve_trading_mode(env)
    refused = sorted(name for name, value in env.items() if value and _is_order_name(name))
    if refused:
        # Names are configuration keys, not values; values are never echoed.
        raise OrderCapabilityError(
            "order/trading configuration is refused in this research-only project "
            f"(ADR-0001): {', '.join(refused)}"
        )
    return RuntimeSafety(trading_mode=mode)


def _is_order_name(name: str) -> bool:
    return _ORDER_CAPABILITY_NAME.match(name) is not None
