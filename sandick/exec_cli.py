"""Execution CLI: verify vault/dex access, then run the basket.

    # Read-only: prove the vault can see the HIP-3 dex and collateral (testnet).
    python -m sandick.exec_cli verify --basket config/sandick.basket.json

    # Preview the live orders (no send) — safe without credentials:
    python -m sandick.exec_cli run --capital 70000 --prices config/prices.example.json

    # Actually send, on testnet, with a slippage + notional cap:
    python -m sandick.exec_cli run --capital 70000 --live \
        --execute --max-notional 80000

Credentials (live/execute only) come from the environment:
    HL_SECRET_KEY        API/agent wallet private key (signs for the vault)
    HL_VAULT_ADDRESS     the native vault address to trade on behalf of
    HL_ACCOUNT_ADDRESS   (optional) master account address
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .allocator import build_plan
from .basket import DEFAULT_BASKET_PATH, Basket
from .execute import (
    Credentials,
    ExecConfig,
    OrderIntent,
    check_safety,
    plan_to_intents,
    submit,
)
from .prices import fetch_live_prices, load_prices_file


def _resolve_prices(basket: Basket, args):
    if args.live:
        return fetch_live_prices(basket.coins, dex=basket.dex, mainnet=not args.testnet)
    if args.prices:
        return load_prices_file(args.prices)
    raise ValueError("provide --prices <file> or --live")


def _render_intents(intents: list[OrderIntent]) -> str:
    lines = ["  COIN                  SIDE   LEV        SIZE     LIMIT_PX      NOTIONAL"]
    lines.append("  " + "-" * 70)
    for i in intents:
        lines.append(
            f"  {i.coin:<20}  {'BUY' if i.is_buy else 'SELL':<5}{i.leverage:>4}x"
            f"{i.size:>12,.4f}{i.limit_px:>13,.4f}{('$%0.2f' % i.notional):>14}"
        )
    lines.append("  " + "-" * 70)
    lines.append(f"  TOTAL gross notional: ${sum(i.notional for i in intents):,.2f}")
    return "\n".join(lines)


def cmd_verify(args) -> int:
    basket = Basket.load(args.basket)
    try:
        from hyperliquid.info import Info
        from hyperliquid.utils import constants
    except ImportError:
        print("error: hyperliquid-python-sdk not installed", file=sys.stderr)
        return 2

    base_url = constants.TESTNET_API_URL if not args.mainnet else constants.MAINNET_API_URL
    net = "mainnet" if args.mainnet else "testnet"
    print(f"Verifying basket '{basket.name}' on dex '{basket.dex}' ({net})...")

    info = Info(base_url, skip_ws=True)
    ok = True

    # 1. The dex exists and exposes our coins.
    try:
        meta = info.meta(dex=basket.dex)
        universe = {a["name"] for a in meta.get("universe", [])}
        missing = [c for c in basket.coins if c not in universe]
        if missing:
            ok = False
            print(f"  [FAIL] coins not on dex: {missing}")
        else:
            print(f"  [ OK ] all {len(basket.coins)} basket coins present on dex")
    except Exception as exc:
        ok = False
        print(f"  [FAIL] could not load dex meta: {exc}")

    # 2. The vault account is readable and has collateral.
    vault = (Credentials.from_env().vault_address if args.use_env else args.vault)
    if vault:
        try:
            state = info.user_state(vault, dex=basket.dex)
            withdrawable = state.get("withdrawable", "?")
            print(f"  [ OK ] vault {vault[:10]}... readable; withdrawable={withdrawable}")
        except Exception as exc:
            ok = False
            print(f"  [FAIL] could not read vault state: {exc}")
    else:
        print("  [SKIP] no vault address (pass --vault or --use-env) — "
              "skipping vault-state and order checks")

    print("\nVerification:", "PASSED" if ok else "FAILED")
    print(
        "Note: placing a tiny test order is the only way to fully prove a vault "
        "can trade this HIP-3 dex. Use `run --execute` with a small --capital on "
        "testnet for that."
    )
    return 0 if ok else 1


def cmd_run(args) -> int:
    basket = Basket.load(args.basket)
    try:
        prices = _resolve_prices(basket, args)
        plan = build_plan(
            basket, prices, capital=args.capital, leverage=args.leverage, side=args.side
        )
        intents = plan_to_intents(plan, slippage=args.slippage)
        check_safety(intents, args.max_notional)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(_render_intents(intents))

    if not args.execute:
        print("\n(preview only — pass --execute to send)")
        return 0

    net = "MAINNET" if args.mainnet else "testnet"
    print(f"\nAbout to send {len(intents)} orders on {net} for vault.")
    if not args.yes:
        reply = input("Type 'yes' to confirm: ").strip().lower()
        if reply != "yes":
            print("aborted.")
            return 1

    config = ExecConfig(
        testnet=not args.mainnet,
        slippage=args.slippage,
        max_notional=args.max_notional,
        confirm=True,
    )
    try:
        results = submit(intents, config)
    except Exception as exc:
        print(f"error: submission failed: {exc}", file=sys.stderr)
        return 2

    for r in results:
        print(r)
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sandick-exec", description="HIP-3 vault execution.")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("verify", help="Read-only vault/dex access checks.")
    v.add_argument("--basket", type=Path, default=DEFAULT_BASKET_PATH)
    v.add_argument("--mainnet", action="store_true", help="Check mainnet (default testnet).")
    v.add_argument("--vault", help="Vault address to check.")
    v.add_argument("--use-env", action="store_true", help="Read vault from HL_VAULT_ADDRESS.")
    v.set_defaults(func=cmd_verify)

    r = sub.add_parser("run", help="Plan and (optionally) place the basket orders.")
    r.add_argument("--capital", type=float, required=True)
    r.add_argument("--leverage", type=float, default=1.0)
    r.add_argument("--side", choices=("long", "short"), default="long")
    r.add_argument("--basket", type=Path, default=DEFAULT_BASKET_PATH)
    r.add_argument("--prices", type=Path)
    r.add_argument("--live", action="store_true")
    r.add_argument("--slippage", type=float, default=0.02)
    r.add_argument("--max-notional", type=float)
    r.add_argument("--execute", action="store_true", help="Actually send (default: preview).")
    r.add_argument("--mainnet", action="store_true", help="Send on mainnet (default testnet).")
    r.add_argument("--yes", action="store_true", help="Skip the interactive confirm.")
    r.set_defaults(func=cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
