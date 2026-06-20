"""Equal-weighted position sizing for a HIP-3 basket vault.

Given a capital amount (USDC used as margin), a leverage multiple and a set of
mark prices, this computes the orders needed to hold an *equal-weighted*
position across every asset in the basket: each asset gets the same fraction of
the gross notional (1 / N).

This module is pure arithmetic and has no network or SDK dependency, so it is
trivially unit-testable and safe to run anywhere. Nothing here places orders.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List

from .basket import Basket, BasketAsset


def round_size(raw_size: float, sz_decimals: int) -> float:
    """Round a size down to the asset's size precision (never over-buy)."""
    if sz_decimals < 0:
        raise ValueError("sz_decimals must be >= 0")
    factor = 10 ** sz_decimals
    return math.floor(raw_size * factor) / factor


@dataclass(frozen=True)
class PlannedOrder:
    """A single equal-weight order in the plan (not yet sent anywhere)."""

    asset: BasketAsset
    side: str  # "long" or "short"
    price: float
    target_weight: float        # intended fraction of gross notional (1/N)
    target_notional: float      # gross notional we aimed to deploy on this asset
    size: float                 # rounded contract size
    notional: float             # size * price (actual)
    margin: float               # notional / leverage
    actual_weight: float        # notional / total actual notional


@dataclass(frozen=True)
class AllocationPlan:
    """The full equal-weighted plan for a basket."""

    basket: Basket
    side: str
    capital: float
    leverage: float
    orders: List[PlannedOrder]

    @property
    def gross_notional(self) -> float:
        return sum(o.notional for o in self.orders)

    @property
    def deployed_margin(self) -> float:
        return sum(o.margin for o in self.orders)

    @property
    def residual_cash(self) -> float:
        """Capital left undeployed due to size rounding."""
        return self.capital - self.deployed_margin


def build_equal_weight_plan(
    basket: Basket,
    prices: Dict[str, float],
    capital: float,
    leverage: float = 1.0,
    side: str = "long",
) -> AllocationPlan:
    """Build an equal-weighted allocation plan.

    Args:
        basket: the basket of assets to size.
        prices: mark price per coin symbol (must cover every basket coin).
        capital: margin capital in USDC to deploy.
        leverage: gross-notional / capital multiple (1.0 = no leverage).
        side: "long" or "short" for every leg.
    """
    if capital <= 0:
        raise ValueError("capital must be > 0")
    if leverage <= 0:
        raise ValueError("leverage must be > 0")
    if side not in ("long", "short"):
        raise ValueError("side must be 'long' or 'short'")

    missing = [c for c in basket.coins if c not in prices]
    if missing:
        raise KeyError(f"Missing prices for: {missing}")
    bad = [c for c in basket.coins if prices[c] <= 0]
    if bad:
        raise ValueError(f"Non-positive prices for: {bad}")

    n = len(basket.assets)
    target_weight = 1.0 / n
    gross_notional = capital * leverage
    per_asset_notional = gross_notional / n

    orders: List[PlannedOrder] = []
    for asset in basket.assets:
        price = float(prices[asset.coin])
        raw_size = per_asset_notional / price
        size = round_size(raw_size, asset.sz_decimals)
        notional = size * price
        orders.append(
            PlannedOrder(
                asset=asset,
                side=side,
                price=price,
                target_weight=target_weight,
                target_notional=per_asset_notional,
                size=size,
                notional=notional,
                margin=notional / leverage,
                actual_weight=0.0,  # filled in below once totals are known
            )
        )

    total_notional = sum(o.notional for o in orders) or 1.0
    orders = [
        PlannedOrder(
            asset=o.asset,
            side=o.side,
            price=o.price,
            target_weight=o.target_weight,
            target_notional=o.target_notional,
            size=o.size,
            notional=o.notional,
            margin=o.margin,
            actual_weight=o.notional / total_notional,
        )
        for o in orders
    ]

    return AllocationPlan(
        basket=basket,
        side=side,
        capital=capital,
        leverage=leverage,
        orders=orders,
    )
