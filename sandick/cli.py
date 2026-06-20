"""Command-line dry-run for the SANDICK equal-weighted HIP-3 vault.

Examples:
    # Dry-run with prices from a file (no network needed):
    python -m sandick.cli --capital 70000 --prices config/prices.example.json

    # Dry-run pulling live prices from a HIP-3 perp dex (needs allowlisted host):
    python -m sandick.cli --capital 70000 --live

This command NEVER places orders. It only prints the orders it *would* submit.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .allocator import AllocationPlan, build_plan
from .basket import DEFAULT_BASKET_PATH, Basket
from .plan import write_plan
from .prices import fetch_live_prices, load_prices_file


def _fmt_usd(x: float) -> str:
    return f"${x:,.2f}"


def render_plan(plan: AllocationPlan) -> str:
    lines = []
    lines.append("=" * 78)
    lines.append(f"  SANDICK HIP-3 VAULT — EQUAL-WEIGHTED PLAN  (DRY RUN — no orders sent)")
    lines.append("=" * 78)
    lines.append(
        f"  Basket: {plan.basket.name}    Dex: {plan.basket.dex or '(core)'}    "
        f"Assets: {len(plan.orders)}"
    )
    lines.append(
        f"  Capital: {_fmt_usd(plan.capital)}    Leverage: {plan.leverage:g}x    "
        f"Side: {plan.side.upper()}    Gross notional: {_fmt_usd(plan.gross_notional)}"
    )
    lines.append("-" * 78)
    header = (
        f"  {'TICKER':<8}{'COIN':<8}{'SIDE':<6}{'LEV':>5}{'PRICE':>11}"
        f"{'SIZE':>12}{'NOTIONAL':>15}{'WEIGHT':>8}"
    )
    lines.append(header)
    lines.append("-" * 78)
    for o in plan.orders:
        lines.append(
            f"  {o.asset.ticker:<8}{o.asset.coin:<8}{o.side.upper():<6}"
            f"{o.leverage:>4g}x{o.price:>11,.2f}{o.size:>12,.{o.asset.sz_decimals}f}"
            f"{_fmt_usd(o.notional):>15}{o.actual_weight * 100:>7.2f}%"
        )
    lines.append("-" * 78)
    lines.append(
        f"  {'TOTAL':<33}{'':>11}{'':>12}{_fmt_usd(plan.gross_notional):>15}"
        f"{sum(o.actual_weight for o in plan.orders) * 100:>7.2f}%"
    )
    lines.append(
        f"  Deployed margin: {_fmt_usd(plan.deployed_margin)}    "
        f"Residual cash (rounding): {_fmt_usd(plan.residual_cash)}"
    )
    lines.append("=" * 78)
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sandick",
        description="Plan an equal-weighted SANDICK HIP-3 basket position (dry-run only).",
    )
    p.add_argument("--capital", type=float, required=True, help="Margin capital in USDC.")
    p.add_argument("--leverage", type=float, default=1.0, help="Leverage multiple (default 1.0).")
    p.add_argument(
        "--side", choices=("long", "short"), default="long", help="Position side (default long)."
    )
    p.add_argument(
        "--basket",
        type=Path,
        default=DEFAULT_BASKET_PATH,
        help="Path to the basket JSON definition.",
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--prices", type=Path, help="Path to a {coin: price} JSON file.")
    src.add_argument(
        "--live",
        action="store_true",
        help="Fetch live mid prices from Hyperliquid (requires allowlisted host).",
    )
    p.add_argument(
        "--testnet", action="store_true", help="With --live, use testnet instead of mainnet."
    )
    p.add_argument(
        "--out", type=Path, help="Write the plan to this JSON file (reviewable artifact)."
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    basket = Basket.load(args.basket)

    if args.live:
        try:
            prices = fetch_live_prices(
                basket.coins, dex=basket.dex, mainnet=not args.testnet
            )
        except Exception as exc:  # surface a clean message rather than a traceback
            print(f"error: failed to fetch live prices: {exc}", file=sys.stderr)
            return 2
    elif args.prices:
        prices = load_prices_file(args.prices)
    else:
        print(
            "error: provide a price source: either --prices <file> or --live",
            file=sys.stderr,
        )
        return 2

    try:
        plan = build_plan(
            basket, prices, capital=args.capital, leverage=args.leverage, side=args.side
        )
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(render_plan(plan))
    if args.out:
        write_plan(plan, str(args.out))
        print(f"\nSaved plan artifact to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
