"""Command-line runner for the keeper bot.

Assembles the keeper's config from the artifacts you already have — the basket
file (target weights + size precision) and the deploy config (`assetIds`) — wires
a live :class:`~sandick.keeper_chain.Web3KeeperClient`, and runs
:func:`~sandick.keeper_bot.run_loop`.

Safety mirrors ``exec_cli``: **preview (dry-run) by default**; nothing is
transmitted unless ``--execute`` is passed (and a manager key is present). Reads
still hit the chain in preview so you can watch what the keeper *would* do.

    # Preview one tick against testnet (no key needed, nothing sent):
    RPC_URL=… VAULT_ADDRESS=0x… USDC_ADDRESS=0x… \
      python -m sandick.keeper_cli --once

    # Run live, transmitting (manager key required):
    RPC_URL=… VAULT_ADDRESS=0x… USDC_ADDRESS=0x… MANAGER_KEY=0x… \
      python -m sandick.keeper_cli --execute --interval 60
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .basket import DEFAULT_BASKET_PATH, Basket
from .keeper import KeeperConfig
from .keeper_bot import KeeperBot, KeeperReport, run_loop
from .weights import resolve_weights

DEFAULT_DEPLOY_PATH = Path("config/deploy.json")


@dataclass(frozen=True)
class KeeperInputs:
    """Everything the bot needs about the basket, derived from local config."""

    target_weights: Dict[str, float]
    sz_decimals: Dict[str, int]
    asset_ids: Dict[str, int]
    coins: List[str]
    dex: str


def load_keeper_inputs(
    basket_path: Path | str = DEFAULT_BASKET_PATH,
    deploy_path: Path | str = DEFAULT_DEPLOY_PATH,
) -> KeeperInputs:
    """Build :class:`KeeperInputs` from the basket + deploy-config files.

    Raises:
        KeyError: if the deploy config is missing an ``assetId`` for any basket coin.
    """
    basket = Basket.load(basket_path)
    weights = resolve_weights(basket)
    sz_decimals = {a.coin: a.sz_decimals for a in basket.assets}

    with open(deploy_path, "r", encoding="utf-8") as fh:
        deploy = json.load(fh)
    all_ids = deploy.get("assetIds", {})
    missing = [c for c in basket.coins if c not in all_ids]
    if missing:
        raise KeyError(
            f"deploy config {str(deploy_path)!r} is missing assetIds for: {missing}. "
            "Regenerate it with: python -m sandick.deploy_config"
        )
    asset_ids = {c: int(all_ids[c]) for c in basket.coins}
    return KeeperInputs(
        target_weights=weights,
        sz_decimals=sz_decimals,
        asset_ids=asset_ids,
        coins=list(basket.coins),
        dex=basket.dex,
    )


def build_bot(
    inputs: KeeperInputs,
    client,
    *,
    execute: bool,
    side: str = "long",
    buffer_fraction: float = 0.05,
    drift_threshold: float = 0.02,
    slippage: float = 0.005,
    max_retries: int = 3,
) -> KeeperBot:
    """Assemble a :class:`KeeperBot`. ``execute=False`` keeps it in dry-run."""
    return KeeperBot(
        client=client,
        target_weights=inputs.target_weights,
        sz_decimals=inputs.sz_decimals,
        asset_ids=inputs.asset_ids,
        side=side,
        slippage=slippage,
        config=KeeperConfig(buffer_fraction=buffer_fraction, drift_threshold=drift_threshold),
        max_retries=max_retries,
        dry_run=not execute,
    )


def format_report(report: KeeperReport) -> str:
    """One-line human summary of a tick (for the loop's ``on_report``)."""
    if report.blockers:
        # Gate-blocked or a crashed tick — must not read as "ok" on the console.
        return "BLOCKED: " + "; ".join(report.blockers)
    liq, reb = report.liquidity, report.rebalance
    parts: List[str] = []
    if liq.bridged > 0:
        if liq.submitted:
            tag = "✓" if liq.verified else "UNVERIFIED"
            parts.append(f"liquidity: bridged {liq.bridged:.2f} ({tag})")
        else:
            parts.append(f"liquidity: would bridge {liq.bridged:.2f} [preview]")
    else:
        parts.append("liquidity: ok")
    if liq.shortfall > 0:
        parts.append(f"shortfall {liq.shortfall:.2f}")
    if reb.triggered:
        if reb.submitted:
            tag = "✓" if reb.verified else "UNVERIFIED"
            parts.append(f"rebalance: {len(reb.orders)} legs ({tag})")
        else:
            parts.append(f"rebalance: {len(reb.orders)} legs [preview]")
    else:
        parts.append("rebalance: ok")
    return " | ".join(parts)


def format_report_json(report: KeeperReport) -> Dict:
    """Machine-readable health snapshot of a tick (for ``--health-out`` / monitoring).

    ``healthy`` is False if the fail-closed gate blocked, an action came back
    UNVERIFIED, or there is an unmet redemption shortfall — i.e. the conditions an
    operator's alerting should page on.
    """
    liq, reb = report.liquidity, report.rebalance
    healthy = (
        not report.blockers
        and "UNVERIFIED" not in liq.note
        and "UNVERIFIED" not in reb.note
        and liq.shortfall == 0
    )
    return {
        "healthy": healthy,
        "blockers": list(report.blockers),
        "liquidity": {
            "bridged": liq.bridged, "shortfall": liq.shortfall,
            "submitted": liq.submitted, "verified": liq.verified, "note": liq.note,
        },
        "rebalance": {
            "triggered": reb.triggered, "legs": len(reb.orders),
            "submitted": reb.submitted, "verified": reb.verified, "note": reb.note,
        },
    }


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sandick-keeper", description="Run the SANDICK keeper bot.")
    p.add_argument("--basket", type=Path, default=DEFAULT_BASKET_PATH, help="Basket config.")
    p.add_argument("--deploy", type=Path, default=DEFAULT_DEPLOY_PATH, help="Deploy config (assetIds).")
    p.add_argument("--rpc-url", help="HyperEVM RPC URL (or env RPC_URL).")
    p.add_argument("--vault", help="Vault address (or env VAULT_ADDRESS / HL_VAULT_ADDRESS).")
    p.add_argument("--usdc", help="Underlying USDC address (or env USDC_ADDRESS).")
    p.add_argument("--side", choices=("long", "short"), default="long")
    p.add_argument("--buffer-fraction", type=float, default=0.05, help="Idle buffer as a fraction of NAV.")
    p.add_argument("--drift-threshold", type=float, default=0.02, help="Max weight drift before rebalance.")
    p.add_argument("--slippage", type=float, default=0.005, help="Marketable-limit slippage for rebalances.")
    p.add_argument("--interval", type=float, default=60.0, help="Seconds between ticks.")
    p.add_argument("--once", action="store_true", help="Run a single tick and exit.")
    p.add_argument("--max-ticks", type=int, help="Stop after N ticks (default: run forever).")
    p.add_argument("--mainnet", action="store_true", help="Use mainnet market data (default testnet).")
    p.add_argument("--execute", action="store_true", help="Actually transmit (default: preview).")
    p.add_argument("--health-out", help="Write a machine-readable health snapshot JSON each tick. "
                                        "With --health-out the process exits non-zero when the last tick is unhealthy.")
    return p


def _resolve(value: Optional[str], *env_vars: str) -> Optional[str]:
    if value:
        return value
    for var in env_vars:
        if os.environ.get(var):
            return os.environ[var]
    return None


def main(argv: Optional[List[str]] = None, *, client_factory: Optional[Callable] = None) -> int:
    """Entry point. ``client_factory`` is injectable for testing (defaults to the
    live :meth:`Web3KeeperClient.from_endpoint`)."""
    args = build_arg_parser().parse_args(argv)

    try:
        inputs = load_keeper_inputs(args.basket, args.deploy)
    except FileNotFoundError as exc:
        print(f"error: config file not found: {exc.filename}. "
              "Generate the deploy config with: python -m sandick.deploy_config")
        return 2
    except KeyError as exc:
        print(f"error: {exc.args[0] if exc.args else exc}")
        return 2

    rpc_url = _resolve(args.rpc_url, "RPC_URL")
    vault = _resolve(args.vault, "VAULT_ADDRESS", "HL_VAULT_ADDRESS")
    usdc = _resolve(args.usdc, "USDC_ADDRESS")
    if not (rpc_url and vault and usdc):
        print("error: need --rpc-url/--vault/--usdc (or RPC_URL/VAULT_ADDRESS/USDC_ADDRESS).")
        return 2

    private_key = _resolve(None, "MANAGER_KEY", "HL_SECRET_KEY")
    if args.execute and not private_key:
        print("error: --execute requires a manager key in MANAGER_KEY (or HL_SECRET_KEY).")
        return 2

    if client_factory is None:  # pragma: no cover - the live wiring needs web3 + a node
        from .keeper_chain import HyperliquidMarketData, Web3KeeperClient

        market_data = HyperliquidMarketData(vault, inputs.coins, dex=inputs.dex, mainnet=args.mainnet)
        client = Web3KeeperClient.from_endpoint(
            rpc_url, vault, usdc,
            private_key=private_key if args.execute else None,
            market_data=market_data,
        )
    else:
        client = client_factory(
            rpc_url=rpc_url, vault=vault, usdc=usdc,
            private_key=private_key if args.execute else None,
            inputs=inputs, mainnet=args.mainnet,
        )

    bot = build_bot(
        inputs, client,
        execute=args.execute, side=args.side,
        buffer_fraction=args.buffer_fraction, drift_threshold=args.drift_threshold,
        slippage=args.slippage,
    )

    mode = "EXECUTE — transmitting" if args.execute else "PREVIEW — nothing sent"
    print(f"SANDICK keeper [{mode}] · vault {vault} · {len(inputs.coins)} assets · "
          f"buffer {args.buffer_fraction:.0%} · drift {args.drift_threshold:.0%}")

    health = {"healthy": True}

    def on_report(r: KeeperReport) -> None:
        print(format_report(r))
        snap = format_report_json(r)
        # Track health unconditionally so a crashed/gate-blocked tick is reflected
        # in the exit code even without --health-out (run_loop turns a tick crash
        # into an unhealthy report rather than propagating it). --health-out only
        # controls whether the snapshot is also persisted for monitoring.
        health["healthy"] = snap["healthy"]
        if args.health_out:
            Path(args.health_out).write_text(json.dumps(snap, indent=2), encoding="utf-8")

    max_ticks = 1 if args.once else args.max_ticks
    run_loop(bot, interval=args.interval, max_ticks=max_ticks, on_report=on_report)

    # Surface the last tick's health as the exit code so the keeper is
    # cron/CI-monitorable: a hard fault or gate-block exits non-zero.
    return 0 if health["healthy"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
