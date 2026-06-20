"""Admin tooling: discover HIP-3 assets and assemble a basket.

Only the admin uses this. Depositors never touch it — they only deposit into
the resulting vault.

Subcommands:
    discover       List every HIP-3 asset available across all perp dexes.
    build-basket   Select assets into an equal-weighted basket config.

Examples:
    # Snapshot the live catalog to a file (needs allowlisted host):
    python -m sandick.admin discover --out catalog.json

    # Build a basket by selecting assets (offline, from the snapshot):
    python -m sandick.admin build-basket \
        --select SNDK,ARM,NBIS,DELL,INTC,CRWV,SKHYNIX \
        --dex sandick --name SANDICK \
        --catalog catalog.json --out config/sandick.basket.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

from .discovery import AssetInfo, discover_assets, flatten


def catalog_to_json(catalog: Dict[str, List[AssetInfo]]) -> dict:
    return {
        "dexs": {
            dex: [
                {
                    "coin": a.coin,
                    "sz_decimals": a.sz_decimals,
                    "max_leverage": a.max_leverage,
                }
                for a in assets
            ]
            for dex, assets in catalog.items()
        }
    }


def catalog_from_json(data: dict) -> Dict[str, List[AssetInfo]]:
    out: Dict[str, List[AssetInfo]] = {}
    for dex, assets in data.get("dexs", {}).items():
        out[dex] = [
            AssetInfo(
                dex=dex,
                coin=a["coin"],
                sz_decimals=int(a.get("sz_decimals", 2)),
                max_leverage=a.get("max_leverage"),
            )
            for a in assets
        ]
    return out


def resolve_selection(
    catalog: Dict[str, List[AssetInfo]],
    selection: List[str],
    dex_hint: str | None = None,
) -> List[AssetInfo]:
    """Map selected coin symbols to AssetInfo, erroring on miss/ambiguity.

    A selection item may be ``"COIN"`` or ``"dex:COIN"``. ``dex_hint`` (from
    ``--dex``) is used to disambiguate bare coins when set.
    """
    by_key = flatten(catalog)
    resolved: List[AssetInfo] = []
    for item in selection:
        if ":" in item:
            dex, coin = item.split(":", 1)
            matches = [a for a in by_key if a.dex == dex and a.coin == coin]
        else:
            coin = item
            matches = [a for a in by_key if a.coin == coin]
            if dex_hint is not None and len(matches) > 1:
                matches = [a for a in matches if a.dex == dex_hint]
        if not matches:
            raise KeyError(f"asset {item!r} not found in catalog")
        if len(matches) > 1:
            where = ", ".join(m.qualified for m in matches)
            raise ValueError(
                f"asset {item!r} is ambiguous across dexes ({where}); "
                "qualify it as 'dex:COIN'"
            )
        resolved.append(matches[0])
    return resolved


def parse_kv(spec: str | None) -> Dict[str, str]:
    """Parse a 'k=v,k=v' string into a dict (empty for None/'')."""
    out: Dict[str, str] = {}
    for pair in (spec or "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"expected 'key=value', got {pair!r}")
        k, v = pair.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def basket_dict_from_assets(
    assets: List[AssetInfo],
    name: str,
    dex: str,
    weights: Dict[str, str] | None = None,
    groups: Dict[str, str] | None = None,
    labels: Dict[str, str] | None = None,
    group_weights: Dict[str, str] | None = None,
) -> dict:
    weights = weights or {}
    groups = groups or {}
    labels = labels or {}

    asset_dicts = []
    for a in assets:
        entry = {
            "company": labels.get(a.coin, a.coin),
            "ticker": a.coin,
            "coin": a.coin,
            "sz_decimals": a.sz_decimals,
        }
        if a.max_leverage is not None:
            entry["max_leverage"] = a.max_leverage
        if a.coin in weights:
            entry["weight"] = float(weights[a.coin])
        if a.coin in groups:
            entry["group"] = groups[a.coin]
        asset_dicts.append(entry)

    basket: dict = {"name": name, "dex": dex, "assets": asset_dicts}
    if group_weights:
        basket["groups"] = {k: float(v) for k, v in group_weights.items()}
    return basket


def _load_catalog(args) -> Dict[str, List[AssetInfo]]:
    if args.catalog:
        with open(args.catalog, "r", encoding="utf-8") as fh:
            return catalog_from_json(json.load(fh))
    return discover_assets(mainnet=not args.testnet)


def cmd_discover(args) -> int:
    try:
        catalog = discover_assets(mainnet=not args.testnet)
    except Exception as exc:
        print(f"error: discovery failed: {exc}", file=sys.stderr)
        return 2

    assets = flatten(catalog)
    print(f"{'DEX':<16}{'COIN':<12}{'szDec':>6}{'maxLev':>8}")
    print("-" * 42)
    for a in assets:
        print(
            f"{(a.dex or '(core)'):<16}{a.coin:<12}{a.sz_decimals:>6}"
            f"{(a.max_leverage if a.max_leverage is not None else '-'):>8}"
        )
    print(f"\n{len(assets)} assets across {len(catalog)} dex(es).")

    if args.out:
        Path(args.out).write_text(json.dumps(catalog_to_json(catalog), indent=2))
        print(f"Saved catalog snapshot to {args.out}")
    return 0


def cmd_build_basket(args) -> int:
    try:
        catalog = _load_catalog(args)
    except Exception as exc:
        print(f"error: could not load catalog: {exc}", file=sys.stderr)
        return 2

    selection = [s.strip() for s in args.select.split(",") if s.strip()]
    if not selection:
        print("error: --select must list at least one asset", file=sys.stderr)
        return 2

    try:
        assets = resolve_selection(catalog, selection, dex_hint=args.dex)
        basket = basket_dict_from_assets(
            assets,
            name=args.name,
            dex=args.dex or "",
            weights=parse_kv(args.weights),
            groups=parse_kv(args.group),
            labels=parse_kv(args.label),
            group_weights=parse_kv(args.group_weights),
        )
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(basket, indent=2) + "\n")
    print(
        f"Wrote basket '{args.name}' with {len(assets)} equal-weighted assets to {out}"
    )
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sandick-admin", description="Admin basket tooling.")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="List available HIP-3 assets.")
    d.add_argument("--testnet", action="store_true")
    d.add_argument("--out", help="Save the catalog snapshot to this JSON file.")
    d.set_defaults(func=cmd_discover)

    b = sub.add_parser("build-basket", help="Assemble an equal-weighted basket.")
    b.add_argument("--select", required=True, help="Comma-separated coins or dex:coin.")
    b.add_argument("--dex", default="", help="HIP-3 dex name for the basket.")
    b.add_argument("--name", default="BASKET", help="Basket name.")
    b.add_argument("--out", required=True, help="Output basket JSON path.")
    b.add_argument("--catalog", help="Use a saved catalog snapshot instead of going live.")
    b.add_argument("--testnet", action="store_true")
    b.add_argument(
        "--weights", help="Explicit relative weights, e.g. 'SNDK=2,ARM=1' (default: equal)."
    )
    b.add_argument("--group", help="Assign assets to groups, e.g. 'SNDK=storage,CRWV=compute'.")
    b.add_argument("--group-weights", help="Group relative weights, e.g. 'storage=0.4,compute=0.6'.")
    b.add_argument("--label", help="Display names, e.g. 'SNDK=SanDisk,CRWV=CoreWeave'.")
    b.set_defaults(func=cmd_build_basket)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
