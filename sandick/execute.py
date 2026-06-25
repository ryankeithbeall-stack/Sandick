"""Execution: turn a plan into real orders on a HIP-3 vault.

This is the bridge from the dry-run planner to live trading on a native
Hyperliquid vault that trades a single HIP-3 (builder-deployed) dex — in scope
here, Trade.xyz.

Safety model (this handles pooled depositor funds, so it is deliberately
conservative):
  * **Testnet by default.** Mainnet requires an explicit opt-in.
  * **Nothing sends unless ``confirm=True``.** Otherwise it's a no-op preview.
  * **Marketable limit orders with a slippage cap** — never naked market orders.
  * **A max-notional circuit breaker** rejects oversized plans.
  * Credentials come from the environment only; never from config/args.

The pure helpers (price rounding, slippage, plan->intents, safety checks) are
unit-tested. The thin SDK submission wrapper is not (it needs a live node).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .allocator import AllocationPlan
from .safety import require_tx_allowed

DEFAULT_SLIPPAGE = 0.02  # 2%


def round_price_perp(px: float, sz_decimals: int) -> float:
    """Round a perp price to Hyperliquid's tick rules.

    Perp prices allow at most 5 significant figures and at most
    ``6 - szDecimals`` decimal places (integer prices are always valid).
    """
    if px <= 0:
        raise ValueError("price must be > 0")
    max_dec = max(0, 6 - sz_decimals)
    # decimals needed for 5 significant figures
    sig_dec = 5 - int(math.floor(math.log10(abs(px)))) - 1
    decimals = min(sig_dec, max_dec)
    return round(px, decimals)


def marketable_limit(mark: float, side: str, slippage: float, sz_decimals: int) -> float:
    """A limit price that crosses the spread by ``slippage`` (caps fill price)."""
    if slippage < 0:
        raise ValueError("slippage must be >= 0")
    raw = mark * (1 + slippage) if side == "long" else mark * (1 - slippage)
    return round_price_perp(raw, sz_decimals)


def order_coin(dex: str, coin: str) -> str:
    """Qualified coin name for exchange orders ('dex:COIN'; bare on core dex)."""
    return f"{dex}:{coin}" if dex else coin


@dataclass(frozen=True)
class OrderIntent:
    """A concrete order to submit (post slippage/tick rounding)."""

    coin: str           # qualified, e.g. "tradexyz:SNDK"
    is_buy: bool
    size: float
    limit_px: float
    leverage: int
    notional: float     # size * mark (for safety checks / logging)


def plan_to_intents(
    plan: AllocationPlan, slippage: float = DEFAULT_SLIPPAGE
) -> List[OrderIntent]:
    """Translate a planned allocation into concrete, tick-rounded orders."""
    dex = plan.basket.dex
    is_buy = plan.side == "long"
    intents: List[OrderIntent] = []
    for o in plan.orders:
        if o.size <= 0:
            continue  # nothing to do for a zero-rounded leg
        intents.append(
            OrderIntent(
                coin=order_coin(dex, o.asset.coin),
                is_buy=is_buy,
                size=o.size,
                limit_px=marketable_limit(o.price, o.side, slippage, o.asset.sz_decimals),
                leverage=max(1, int(o.leverage)),
                notional=o.notional,
            )
        )
    return intents


def check_safety(intents: List[OrderIntent], max_notional: Optional[float]) -> None:
    """Raise if the plan violates configured safety limits."""
    if not intents:
        raise ValueError("plan produced no orders to submit")
    total = sum(i.notional for i in intents)
    if max_notional is not None and total > max_notional:
        raise ValueError(
            f"gross notional ${total:,.2f} exceeds --max-notional ${max_notional:,.2f}"
        )


@dataclass(frozen=True)
class ExecConfig:
    testnet: bool = True
    slippage: float = DEFAULT_SLIPPAGE
    max_notional: Optional[float] = None
    confirm: bool = False        # must be True to actually transmit
    tif: str = "Ioc"            # marketable-limit time-in-force


@dataclass(frozen=True)
class Credentials:
    secret_key: str
    account_address: Optional[str]
    vault_address: str

    @classmethod
    def from_env(cls) -> "Credentials":
        secret = os.environ.get("HL_SECRET_KEY")
        vault = os.environ.get("HL_VAULT_ADDRESS")
        if not secret:
            raise EnvironmentError("HL_SECRET_KEY is not set")
        if not vault:
            raise EnvironmentError("HL_VAULT_ADDRESS is not set")
        return cls(
            secret_key=secret,
            account_address=os.environ.get("HL_ACCOUNT_ADDRESS"),
            vault_address=vault,
        )


def _make_exchange(creds: Credentials, testnet: bool):
    """Construct an SDK Exchange that trades on behalf of the vault."""
    from eth_account import Account
    from hyperliquid.exchange import Exchange
    from hyperliquid.utils import constants

    base_url = constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL
    wallet = Account.from_key(creds.secret_key)
    return Exchange(
        wallet,
        base_url,
        vault_address=creds.vault_address,
        account_address=creds.account_address,
    )


def _maybe_float(value: Any) -> Optional[float]:
    """Best-effort float (the SDK returns numeric fields as strings)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def interpret_order_result(res: Any) -> Dict[str, Any]:
    """Classify a Hyperliquid order response into a fail-closed outcome.

    The SDK returns either a top-level error
    (``{"status": "err", "response": "<msg>"}``) or
    ``{"status": "ok", "response": {"data": {"statuses": [<per-order>]}}}`` where
    each per-order entry is one of ``{"filled": {...}}``, ``{"resting": {...}}`` or
    ``{"error": "<msg>"}``. We place one order per call, so we read the first
    status.

    Returns a normalized dict ``{accepted, state, detail, oid, filled_sz,
    avg_px}``. ``accepted`` is ``True`` only when the exchange did not reject the
    order (``filled``/``resting``); anything ambiguous or erroneous is
    ``accepted=False`` so a silent failure can never read as success.
    """
    unknown = {"accepted": False, "state": "unknown", "detail": "",
               "oid": None, "filled_sz": None, "avg_px": None}
    if not isinstance(res, dict):
        return {**unknown, "detail": f"non-dict response: {res!r}"}
    if res.get("status") != "ok":
        return {**unknown, "state": "error",
                "detail": str(res.get("response", res))}
    statuses = (((res.get("response") or {}).get("data") or {}).get("statuses")) or []
    if not statuses:
        return {**unknown, "detail": "ok response carried no order statuses"}
    st = statuses[0]
    if not isinstance(st, dict):
        return {**unknown, "detail": f"unexpected status entry: {st!r}"}
    # Check error FIRST so any error key wins, keeping the classifier fail-closed
    # even against a malformed entry that also carried filled/resting.
    if "error" in st:
        return {**unknown, "state": "rejected", "detail": str(st.get("error"))}
    if "filled" in st:
        f = st.get("filled") or {}
        return {"accepted": True, "state": "filled", "detail": "filled",
                "oid": f.get("oid"),
                "filled_sz": _maybe_float(f.get("totalSz")),
                "avg_px": _maybe_float(f.get("avgPx"))}
    if "resting" in st:
        r = st.get("resting") or {}
        return {"accepted": True, "state": "resting",
                "detail": "resting (accepted, no immediate fill)",
                "oid": r.get("oid"), "filled_sz": 0.0, "avg_px": None}
    return {**unknown, "detail": f"unrecognized status: {st!r}"}


def submit(
    intents: List[OrderIntent], config: ExecConfig, creds: Optional[Credentials] = None
) -> List[dict]:
    """Submit the intents. With ``confirm=False`` this is a no-op preview.

    Returns a list of result dicts (one per leg), each carrying an ``outcome``
    classification from :func:`interpret_order_result` so a rejected or unfilled
    leg can't pass silently as success — the caller (e.g. ``exec_cli run``) checks
    it and surfaces failures. Not unit-tested end to end — requires a live node;
    the classifier and the CLI verification are unit-tested. The per-asset
    leverage is set before each order.
    """
    check_safety(intents, config.max_notional)
    if not config.confirm:
        return [{"status": "preview", "coin": i.coin} for i in intents]

    require_tx_allowed("executor submit")  # hard env gate before any live order
    creds = creds or Credentials.from_env()
    exchange = _make_exchange(creds, config.testnet)

    results: List[dict] = []
    for i in intents:
        # Per-asset leverage (cross margin) before the order.
        exchange.update_leverage(i.leverage, i.coin, True)
        res = exchange.order(
            i.coin,
            i.is_buy,
            i.size,
            i.limit_px,
            {"limit": {"tif": config.tif}},
            reduce_only=False,
        )
        results.append({"coin": i.coin, "result": res, "outcome": interpret_order_result(res)})
    return results
