"""Price sources for the SANDICK basket.

Two sources are supported:

* ``load_prices_file`` — read a JSON map of ``{coin: price}`` from disk. This is
  the default for dry-runs and needs no network access.
* ``fetch_live_prices`` — pull mark/mid prices from a Hyperliquid HIP-3 perp dex
  using the official SDK. This only works where ``api.hyperliquid.xyz`` is
  reachable (i.e. allowlisted in your network egress settings).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List


def load_prices_file(path: Path | str) -> Dict[str, float]:
    """Load a ``{coin: price}`` map from a JSON file."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    # Keys beginning with "_" are treated as metadata/comments and ignored.
    return {str(k): float(v) for k, v in data.items() if not str(k).startswith("_")}


def fetch_live_prices(coins: List[str], dex: str = "", mainnet: bool = True) -> Dict[str, float]:
    """Fetch live mid prices for ``coins`` from a Hyperliquid perp dex.

    Args:
        coins: coin symbols to fetch (e.g. ["SNDK", "ARM", ...]).
        dex: HIP-3 builder dex name. Empty string = the first/core perp dex.
        mainnet: use mainnet (True) or testnet (False).

    Raises:
        ImportError: if the hyperliquid SDK is not installed.
        KeyError: if any requested coin is absent from the dex's mids.
    """
    try:
        from hyperliquid.info import Info
        from hyperliquid.utils import constants
    except ImportError as exc:  # pragma: no cover - exercised only without SDK
        raise ImportError(
            "hyperliquid-python-sdk is required for --live. "
            "Install it with: pip install hyperliquid-python-sdk"
        ) from exc

    base_url = constants.MAINNET_API_URL if mainnet else constants.TESTNET_API_URL
    info = Info(base_url, skip_ws=True)
    mids = info.all_mids(dex=dex)

    missing = [c for c in coins if c not in mids]
    if missing:
        raise KeyError(
            f"Coins not found on dex {dex!r}: {missing}. "
            "Check the dex name and coin symbols in your basket config."
        )
    return {c: float(mids[c]) for c in coins}
