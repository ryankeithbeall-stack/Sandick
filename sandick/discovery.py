"""Discovery of available HIP-3 assets.

An admin builds a basket by *selecting* from the assets that exist across
Hyperliquid's perp dexes — the core dex plus every HIP-3 (builder-deployed)
dex. This module enumerates that catalog.

The parsing functions are pure and unit-tested; the ``discover_*`` wrappers hit
the Hyperliquid API via the official SDK and only work where the host is
reachable (allowlisted egress).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class AssetInfo:
    """A single tradable asset on some perp dex."""

    dex: str                 # "" for the core perp dex
    coin: str                # symbol, e.g. "SNDK"
    sz_decimals: int         # size rounding precision
    max_leverage: Optional[int] = None

    @property
    def qualified(self) -> str:
        """Fully-qualified name: 'dex:coin' (or just 'coin' on the core dex)."""
        return f"{self.dex}:{self.coin}" if self.dex else self.coin


def parse_perp_dexs(raw: Any) -> List[str]:
    """Extract dex names from a ``perpDexs`` response.

    The first element is typically ``null`` (the core dex), which we represent
    as the empty string "".
    """
    names: List[str] = []
    for entry in raw or []:
        if entry is None:
            names.append("")
        elif isinstance(entry, dict):
            names.append(str(entry.get("name", "")))
        else:
            names.append(str(entry))
    return names


def parse_meta_universe(meta: Any, dex: str = "") -> List[AssetInfo]:
    """Extract assets from a ``meta`` response's ``universe`` list."""
    assets: List[AssetInfo] = []
    universe = (meta or {}).get("universe", []) if isinstance(meta, dict) else []
    for entry in universe:
        if not isinstance(entry, dict) or "name" not in entry:
            continue
        # Delisted assets are flagged; skip them so admins can't pick dead markets.
        if entry.get("isDelisted"):
            continue
        ml = entry.get("maxLeverage")
        assets.append(
            AssetInfo(
                dex=dex,
                coin=str(entry["name"]),
                sz_decimals=int(entry.get("szDecimals", 2)),
                max_leverage=int(ml) if ml is not None else None,
            )
        )
    return assets


def discover_assets(
    mainnet: bool = True, include_core: bool = True
) -> Dict[str, List[AssetInfo]]:
    """Enumerate every dex and its assets from a live Hyperliquid node.

    Returns a mapping of ``dex_name -> [AssetInfo, ...]`` (core dex keyed by "").

    Raises:
        ImportError: if the hyperliquid SDK is not installed.
    """
    try:
        from hyperliquid.info import Info
        from hyperliquid.utils import constants
    except ImportError as exc:  # pragma: no cover - only without SDK
        raise ImportError(
            "hyperliquid-python-sdk is required for discovery. "
            "Install it with: pip install hyperliquid-python-sdk"
        ) from exc

    base_url = constants.MAINNET_API_URL if mainnet else constants.TESTNET_API_URL
    info = Info(base_url, skip_ws=True)

    dex_names = parse_perp_dexs(info.post("/info", {"type": "perpDexs"}))
    catalog: Dict[str, List[AssetInfo]] = {}
    for dex in dex_names:
        if dex == "" and not include_core:
            continue
        meta = info.meta(dex=dex)
        catalog[dex] = parse_meta_universe(meta, dex=dex)
    return catalog


def flatten(catalog: Dict[str, List[AssetInfo]]) -> List[AssetInfo]:
    """Flatten a discovery catalog into a single sorted list."""
    out: List[AssetInfo] = []
    for assets in catalog.values():
        out.extend(assets)
    return sorted(out, key=lambda a: (a.dex, a.coin))
