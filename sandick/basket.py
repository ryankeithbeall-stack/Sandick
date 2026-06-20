"""The SANDICK basket: the seven HIP-3 perp markets whose logos spell SANDICK.

Each asset maps a real ticker to the coin symbol used on a Hyperliquid HIP-3
(builder-deployed) perp dex. The coin symbols and ``sz_decimals`` are the
values used when sizing/placing orders; edit ``config/sandick.basket.json`` to
match whatever the perp dex actually deploys.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List

# Default location of the basket definition shipped with the repo.
DEFAULT_BASKET_PATH = Path(__file__).resolve().parent.parent / "config" / "sandick.basket.json"


@dataclass(frozen=True)
class BasketAsset:
    """A single constituent of the SANDICK basket."""

    company: str
    ticker: str
    coin: str
    sz_decimals: int

    @classmethod
    def from_dict(cls, data: dict) -> "BasketAsset":
        return cls(
            company=data["company"],
            ticker=data["ticker"],
            coin=data["coin"],
            sz_decimals=int(data.get("sz_decimals", 2)),
        )


@dataclass(frozen=True)
class Basket:
    """A named collection of assets deployed on a HIP-3 perp dex."""

    name: str
    dex: str
    assets: List[BasketAsset]

    def __post_init__(self) -> None:
        if not self.assets:
            raise ValueError("A basket must contain at least one asset.")
        coins = [a.coin for a in self.assets]
        dupes = {c for c in coins if coins.count(c) > 1}
        if dupes:
            raise ValueError(f"Duplicate coin symbols in basket: {sorted(dupes)}")

    @property
    def coins(self) -> List[str]:
        return [a.coin for a in self.assets]

    @classmethod
    def from_dict(cls, data: dict) -> "Basket":
        return cls(
            name=data.get("name", "SANDICK"),
            dex=data.get("dex", ""),
            assets=[BasketAsset.from_dict(a) for a in data["assets"]],
        )

    @classmethod
    def load(cls, path: Path | str = DEFAULT_BASKET_PATH) -> "Basket":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))
