"""Position sizing for a HIP-3 basket vault.

Given capital (USDC margin), a leverage default and mark prices, this computes
the orders needed to hold the basket at its target weights. Weights come from
the basket config (equal / explicit / grouped — see ``weights.py``); leverage
can be overridden per asset.

Sizing math, where ``wᵢ`` is asset i's target weight and ``Lᵢ`` its leverage::

    gross_notional   = capital / Σ(wᵢ / Lᵢ)      # so Σ marginᵢ == capital
    target_notionalᵢ = wᵢ * gross_notional
    sizeᵢ            = floor(target_notionalᵢ / priceᵢ, sz_decimalsᵢ)
    marginᵢ          = sizeᵢ * priceᵢ / Lᵢ

With a single leverage L this reduces to ``gross_notional = capital * L``.

This module is pure arithmetic — no network, no SDK. Nothing here places orders.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List

from .basket import Basket, BasketAsset
from .weights import resolve_weights


def round_size(raw_size: float, sz_decimals: int) -> float:
    """Round a size down to the asset's size precision (never over-buy)."""
    if sz_decimals < 0:
        raise ValueError("sz_decimals must be >= 0")
    factor = 10 ** sz_decimals
    return math.floor(raw_size * factor) / factor


@dataclass(frozen=True)
class PlannedOrder:
    """A single order in the plan (not yet sent anywhere)."""

    asset: BasketAsset
    side: str                   # "long" or "short"
    price: float
    leverage: float             # effective leverage for this asset
    target_weight: float        # intended fraction of gross notional
    target_notional: float      # gross notional we aimed to deploy on this asset
    size: float                 # rounded contract size
    notional: float             # size * price (actual)
    margin: float               # notional / leverage
    actual_weight: float        # notional / total actual notional


@dataclass(frozen=True)
class AllocationPlan:
    """The full allocation plan for a basket."""

    basket: Basket
    side: str
    capital: float
    leverage: float             # the default/global leverage
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


def build_plan(
    basket: Basket,
    prices: Dict[str, float],
    capital: float,
    leverage: float = 1.0,
    side: str = "long",
) -> AllocationPlan:
    """Build an allocation plan honoring the basket's weights and leverage.

    Args:
        basket: the basket of assets to size.
        prices: mark price per coin symbol (must cover every basket coin).
        capital: margin capital in USDC to deploy.
        leverage: default leverage; per-asset ``leverage`` overrides it.
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

    weights = resolve_weights(basket)

    def lev_for(asset: BasketAsset) -> float:
        lv = asset.leverage if asset.leverage is not None else leverage
        if lv <= 0:
            raise ValueError(f"leverage for {asset.coin} must be > 0")
        if asset.max_leverage is not None and lv > asset.max_leverage:
            raise ValueError(
                f"leverage {lv:g}x for {asset.coin} exceeds exchange max "
                f"{asset.max_leverage}x"
            )
        return lv

    levs = {a.coin: lev_for(a) for a in basket.assets}

    # gross_notional chosen so that the margin across all legs sums to capital.
    denom = sum(weights[a.coin] / levs[a.coin] for a in basket.assets)
    gross_notional = capital / denom

    orders: List[PlannedOrder] = []
    for asset in basket.assets:
        price = float(prices[asset.coin])
        w = weights[asset.coin]
        lv = levs[asset.coin]
        target_notional = w * gross_notional
        size = round_size(target_notional / price, asset.sz_decimals)
        notional = size * price
        orders.append(
            PlannedOrder(
                asset=asset,
                side=side,
                price=price,
                leverage=lv,
                target_weight=w,
                target_notional=target_notional,
                size=size,
                notional=notional,
                margin=notional / lv,
                actual_weight=0.0,  # filled below once totals are known
            )
        )

    total_notional = sum(o.notional for o in orders) or 1.0
    orders = [
        PlannedOrder(
            asset=o.asset,
            side=o.side,
            price=o.price,
            leverage=o.leverage,
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


# Backwards-compatible alias: the plan is equal-weight unless the basket says
# otherwise.
build_equal_weight_plan = build_plan
