"""Target-weight resolution for a basket.

Three modes, auto-detected from the basket config:

* **equal**    — no weights given. Every asset gets 1 / N. (Default.)
* **weighted** — every asset has an explicit ``weight``. Normalized to sum 1.
* **grouped**  — the basket defines ``groups`` (group -> relative weight) and
                 every asset has a ``group``. Each group's weight is split
                 equally among its members.

Returns a ``{coin: weight}`` map that always sums to 1.0.
"""

from __future__ import annotations

from typing import Dict

from .basket import Basket


def resolve_weights(basket: Basket) -> Dict[str, float]:
    assets = basket.assets

    if basket.groups:
        return _grouped_weights(basket)

    explicit = [a for a in assets if a.weight is not None]
    if explicit:
        if len(explicit) != len(assets):
            missing = [a.coin for a in assets if a.weight is None]
            raise ValueError(
                f"weighted basket: these assets are missing a weight: {missing}"
            )
        if any(a.weight < 0 for a in assets):
            raise ValueError("weights must be non-negative")
        total = sum(a.weight for a in assets)
        if total <= 0:
            raise ValueError("sum of weights must be > 0")
        return {a.coin: a.weight / total for a in assets}

    # Equal weight.
    n = len(assets)
    return {a.coin: 1.0 / n for a in assets}


def _grouped_weights(basket: Basket) -> Dict[str, float]:
    groups = basket.groups or {}
    if any(a.group is None for a in basket.assets):
        missing = [a.coin for a in basket.assets if a.group is None]
        raise ValueError(f"grouped basket: assets missing a group: {missing}")

    unknown = {a.group for a in basket.assets} - set(groups)
    if unknown:
        raise ValueError(f"assets reference undefined groups: {sorted(unknown)}")
    if any(w < 0 for w in groups.values()):
        raise ValueError("group weights must be non-negative")

    group_total = sum(groups.values())
    if group_total <= 0:
        raise ValueError("sum of group weights must be > 0")

    members: Dict[str, list] = {}
    for a in basket.assets:
        members.setdefault(a.group, []).append(a.coin)

    weights: Dict[str, float] = {}
    for group, coins in members.items():
        share = (groups[group] / group_total) / len(coins)
        for coin in coins:
            weights[coin] = share
    return weights
