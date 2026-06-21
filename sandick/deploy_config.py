"""Derive the on-chain vault's deploy-time immutables from live Hyperliquid data.

The BasketVault / HyperCoreReader constructors need values that only exist on a
live network: the Trade.xyz perp-dex index, each coin's HIP-3 asset id, USDC's
HyperCore token index + system address, and the EVM<->Core decimal scale. This
module computes them (pure helpers are unit-tested; the live fetch needs the SDK
and an allowlisted host) and writes a deploy-config JSON consumed by deploy.js.
"""

from __future__ import annotations

import json
from typing import Dict, List

from .onchain import hip3_asset_id

# accountMarginSummary read precompile (mainnet/testnet).
ACCOUNT_MARGIN_SUMMARY_PRECOMPILE = "0x000000000000000000000000000000000000080F"


def usdc_system_address(token_index: int) -> str:
    """Token system address: first byte 0x20, token index big-endian in the low bytes."""
    if token_index < 0:
        raise ValueError("token_index must be >= 0")
    value = (0x20 << 152) | token_index
    return "0x" + format(value, "040x")


def core_scale(evm_decimals: int, core_wei_decimals: int) -> int:
    """Multiplier converting an EVM-decimal amount to HyperCore integer units."""
    diff = core_wei_decimals - evm_decimals
    if diff < 0:
        raise ValueError(
            f"core_wei_decimals ({core_wei_decimals}) < evm_decimals ({evm_decimals}); "
            "sub-unit scaling needs explicit handling"
        )
    return 10 ** diff


def asset_ids_for(coins: List[str], universe_names: List[str], perp_dex_index: int) -> Dict[str, int]:
    """Map each coin to its HIP-3 asset id from the dex's meta universe order."""
    index_of = {name: i for i, name in enumerate(universe_names)}
    out: Dict[str, int] = {}
    for coin in coins:
        if coin not in index_of:
            raise KeyError(f"coin {coin!r} not in dex universe")
        out[coin] = hip3_asset_id(perp_dex_index, index_of[coin])
    return out


def find_perp_dex_index(perp_dexs: list, *, name: str | None = None, deployer: str | None = None) -> int:
    """Index of a builder dex in the perpDexs array (element 0 is the null default)."""
    for i, entry in enumerate(perp_dexs):
        if entry is None:
            continue
        if name is not None and entry.get("name") == name:
            return i
        if deployer is not None and str(entry.get("deployer", "")).lower() == deployer.lower():
            return i
    raise KeyError(f"perp dex not found (name={name!r}, deployer={deployer!r})")


def build_deploy_config(
    basket,
    *,
    dex_name: str | None = None,
    deployer: str | None = None,
    evm_usdc_decimals: int = 6,
    testnet: bool = True,
) -> dict:
    """Fetch live data and assemble the deploy config. Requires the SDK + network."""
    from hyperliquid.info import Info
    from hyperliquid.utils import constants

    base = constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL
    info = Info(base, skip_ws=True)

    perp_dexs = info.post("/info", {"type": "perpDexs"})
    perp_dex_index = find_perp_dex_index(
        perp_dexs, name=dex_name or basket.dex or None, deployer=deployer
    )

    meta = info.meta(dex=basket.dex)
    universe_names = [a["name"] for a in meta.get("universe", [])]
    asset_ids = asset_ids_for(basket.coins, universe_names, perp_dex_index)

    spot_meta = info.spot_meta()
    usdc = next(t for t in spot_meta["tokens"] if t["name"] == "USDC")
    token_index = int(usdc["index"])
    core_wei_decimals = int(usdc["weiDecimals"])

    return {
        "network": "testnet" if testnet else "mainnet",
        "basket": basket.name,
        "dex": basket.dex,
        "perpDexIndex": perp_dex_index,
        "assetIds": asset_ids,
        "marginSummaryPrecompile": ACCOUNT_MARGIN_SUMMARY_PRECOMPILE,
        "usdcCoreTokenIndex": token_index,
        "usdcSystemAddress": usdc_system_address(token_index),
        "coreScale": core_scale(evm_usdc_decimals, core_wei_decimals),
        "tif": 3,  # IOC
    }


def write_deploy_config(config: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
        fh.write("\n")


def _main(argv=None) -> int:
    import argparse

    from .basket import DEFAULT_BASKET_PATH, Basket

    p = argparse.ArgumentParser(
        prog="sandick-deploy-config",
        description="Generate the on-chain deploy config from live Hyperliquid data.",
    )
    p.add_argument("--basket", default=str(DEFAULT_BASKET_PATH))
    p.add_argument("--dex-name", help="perpDexs name to match (defaults to basket.dex).")
    p.add_argument("--deployer", help="perpDexs deployer address to match instead of name.")
    p.add_argument("--out", default="config/deploy.json")
    p.add_argument("--mainnet", action="store_true")
    args = p.parse_args(argv)

    basket = Basket.load(args.basket)
    cfg = build_deploy_config(
        basket, dex_name=args.dex_name, deployer=args.deployer, testnet=not args.mainnet
    )
    write_deploy_config(cfg, args.out)
    print(f"Wrote {args.out} (perpDexIndex={cfg['perpDexIndex']}, {len(cfg['assetIds'])} assets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
