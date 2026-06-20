"""Rebalance mode: trade only the deltas back to target.

Given target positions (from an equal-weight plan) and the current on-chain
positions, compute the minimal set of orders to reach the targets. Reductions
are flagged ``reduce_only`` so they can't accidentally flip a position.

Positions are signed: positive = long, negative = short. Pure and testable;
feeds the same on-chain ``submitBasket`` path as a fresh entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .allocator import AllocationPlan, round_size
from .execute import marketable_limit
from .onchain import OnchainOrder, to_core_int


@dataclass(frozen=True)
class RebalanceOrder:
    coin: str
    is_buy: bool
    size: float          # absolute delta size
    reduce_only: bool


def targets_from_plan(plan: AllocationPlan) -> Dict[str, float]:
    """Signed target size per coin (negative for a short basket)."""
    sign = 1.0 if plan.side == "long" else -1.0
    return {o.asset.coin: sign * o.size for o in plan.orders}


def compute_rebalance(
    targets: Dict[str, float],
    current: Dict[str, float],
    sz_decimals: Dict[str, int],
    min_size: float = 0.0,
) -> List[RebalanceOrder]:
    """Compute delta orders to move ``current`` positions to ``targets``.

    Args:
        targets: signed target size per coin.
        current: signed current size per coin (missing = 0).
        sz_decimals: size precision per coin (for rounding deltas).
        min_size: skip deltas whose absolute size is below this threshold.
    """
    orders: List[RebalanceOrder] = []
    for coin in sorted(set(targets) | set(current)):
        tgt = targets.get(coin, 0.0)
        cur = current.get(coin, 0.0)
        delta = tgt - cur
        dec = sz_decimals.get(coin, 2)
        size = round_size(abs(delta), dec)
        if size <= min_size or size == 0.0:
            continue
        is_buy = delta > 0
        # A trade is reduce-only when it shrinks the existing position toward 0
        # (opposite direction to the current sign) without crossing zero.
        reduce_only = (
            (cur > 0 and not is_buy and tgt >= 0)
            or (cur < 0 and is_buy and tgt <= 0)
        )
        orders.append(
            RebalanceOrder(coin=coin, is_buy=is_buy, size=size, reduce_only=reduce_only)
        )
    return orders


def rebalance_to_onchain(
    orders: List[RebalanceOrder],
    asset_ids: Dict[str, int],
    prices: Dict[str, float],
    sz_decimals: Dict[str, int],
    slippage: float,
) -> List[OnchainOrder]:
    """Convert rebalance deltas into submitBasket-ready on-chain orders."""
    out: List[OnchainOrder] = []
    for o in orders:
        side = "long" if o.is_buy else "short"  # buy crosses up, sell crosses down
        px = marketable_limit(prices[o.coin], side, slippage, sz_decimals[o.coin])
        out.append(
            OnchainOrder(
                asset_id=asset_ids[o.coin],
                is_buy=o.is_buy,
                limit_px=to_core_int(px),
                sz=to_core_int(o.size),
                reduce_only=o.reduce_only,
            )
        )
    return out
