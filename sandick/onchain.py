"""Bridge the off-chain planner to the on-chain vault.

Converts an AllocationPlan into the integer-encoded `Order` tuples that
`SandickVault.submitBasket` expects:

  * asset id   — HIP-3 formula: 100000 + perp_dex_index*10000 + index_in_meta
  * limitPx/sz — HyperCore integers, scaled by 1e8
  * tif/cloid  — handled on-chain (tif is a vault immutable; cloid = 0)

Confirmed against the official asset-id docs and hyper-evm-lib encodings.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List

from .allocator import AllocationPlan
from .execute import marketable_limit

HIP3_BASE = 100_000
HIP3_DEX_BLOCK = 10_000
PX_SZ_SCALE = Decimal(10) ** 8


def hip3_asset_id(perp_dex_index: int, index_in_meta: int) -> int:
    """HIP-3 builder-perp asset id. First builder dex has perp_dex_index=1."""
    if perp_dex_index < 1:
        raise ValueError("perp_dex_index for a builder dex starts at 1")
    if index_in_meta < 0:
        raise ValueError("index_in_meta must be >= 0")
    if index_in_meta >= HIP3_DEX_BLOCK:
        raise ValueError("index_in_meta exceeds the per-dex block size")
    return HIP3_BASE + perp_dex_index * HIP3_DEX_BLOCK + index_in_meta


def to_core_int(value: float) -> int:
    """Scale a human price/size to a HyperCore 1e8 integer (floored)."""
    return int((Decimal(str(value)) * PX_SZ_SCALE).to_integral_value(rounding="ROUND_FLOOR"))


@dataclass(frozen=True)
class OnchainOrder:
    asset_id: int
    is_buy: bool
    limit_px: int  # 1e8-scaled
    sz: int        # 1e8-scaled
    reduce_only: bool

    def as_tuple(self):
        """Tuple in submitBasket's Order field order."""
        return (self.asset_id, self.is_buy, self.limit_px, self.sz, self.reduce_only)


def plan_to_onchain_orders(
    plan: AllocationPlan,
    asset_ids: Dict[str, int],
    slippage: float,
) -> List[OnchainOrder]:
    """Build submitBasket-ready orders from a plan and a coin->assetId map."""
    missing = [o.asset.coin for o in plan.orders if o.asset.coin not in asset_ids]
    if missing:
        raise KeyError(f"missing asset ids for: {missing}")

    is_buy = plan.side == "long"
    out: List[OnchainOrder] = []
    for o in plan.orders:
        if o.size <= 0:
            continue
        px = marketable_limit(o.price, o.side, slippage, o.asset.sz_decimals)
        out.append(
            OnchainOrder(
                asset_id=asset_ids[o.asset.coin],
                is_buy=is_buy,
                limit_px=to_core_int(px),
                sz=to_core_int(o.size),
                reduce_only=False,
            )
        )
    return out
