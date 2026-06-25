"""Keeper bot orchestration for the SANDICK vault.

``keeper.py`` holds the *pure* decision logic (when to bridge, when to
rebalance). This module wires those decisions to the live vault: it reads
on-chain state through an injected client, acts on it (bridge USDC back from
Core, submit rebalance deltas), and — critically — **verifies** each action by
re-reading state, because CoreWriter actions settle later and can fail
silently. A receipt is never treated as success.

The bot talks to the world through the :class:`KeeperClient` protocol, so the
orchestration is fully testable offline with a fake. A real adapter (web3.py
against the HyperEVM vault + read precompiles) implements the same protocol;
that thin adapter is the one piece that needs a live node and is therefore not
exercised by the unit tests.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Protocol

from .keeper import KeeperConfig, needs_rebalance, plan_liquidity, weights_from_positions
from .keeper_guard import KeeperState, evaluate_gate
from .onchain import OnchainOrder
from .rebalance import compute_rebalance, rebalance_to_onchain


# ──────────────────────────────────────────────────────────────────────────
#  Pure helper: target weights -> signed target sizes
# ──────────────────────────────────────────────────────────────────────────
def target_sizes_from_weights(
    target_weights: Dict[str, float],
    prices: Dict[str, float],
    total_notional: float,
    side: str = "long",
) -> Dict[str, float]:
    """Signed target size per coin that holds gross notional constant.

    A drift rebalance keeps the book the same size and only moves the *weights*
    back to target, so each coin's target notional is ``total_notional * w`` and
    its target size is that divided by price, signed by the basket ``side``.

    Args:
        target_weights: desired notional weight per coin (need not sum to 1; it
            is used as-is so callers can express partial baskets).
        prices: mark price per coin (must be positive for weighted coins).
        total_notional: gross notional to spread across the basket.
        side: ``"long"`` (positive sizes) or ``"short"`` (negative sizes).
    """
    if side not in ("long", "short"):
        raise ValueError("side must be 'long' or 'short'")
    if total_notional < 0:
        raise ValueError("total_notional must be >= 0")
    sign = 1.0 if side == "long" else -1.0
    out: Dict[str, float] = {}
    for coin, w in target_weights.items():
        price = prices.get(coin, 0.0)
        if price <= 0:
            raise ValueError(f"price for {coin} must be positive")
        out[coin] = sign * (total_notional * w) / price
    return out


def gross_notional(positions: Dict[str, float], prices: Dict[str, float]) -> float:
    """Sum of absolute notional across the book (a short still adds size)."""
    return sum(abs(sz) * prices.get(coin, 0.0) for coin, sz in positions.items())


# ──────────────────────────────────────────────────────────────────────────
#  Client protocol — the bot's view of the live vault
# ──────────────────────────────────────────────────────────────────────────
class KeeperClient(Protocol):
    """Everything the keeper needs to read and do, behind one seam.

    Reads come from the vault + HyperCore read precompiles; writes go through
    the manager-gated vault entrypoints (``bridgeFromCore`` / ``submitBasket``).
    Implementations must be safe to call repeatedly (the bot retries).
    """

    # ---- reads ----
    def idle_assets(self) -> float: ...
    def pending_redeem_assets(self) -> float: ...
    def nav(self) -> float: ...
    def core_available(self) -> float: ...
    def positions(self) -> Dict[str, float]: ...
    def prices(self) -> Dict[str, float]: ...

    # ---- writes (return a tx hash / id; settlement is confirmed by re-reading) ----
    def bridge_from_core(self, amount: float) -> str: ...
    def submit_basket(self, orders: List[OnchainOrder]) -> str: ...


# ──────────────────────────────────────────────────────────────────────────
#  Reports
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class LiquidityResult:
    bridged: float          # USDC requested from Core (0 if none / dry-run plan)
    shortfall: float        # unmet redemption need (Core can't cover)
    submitted: bool         # a bridge tx was actually sent
    verified: bool          # idle USDC rose by ~the bridged amount afterwards
    tx: Optional[str] = None
    note: str = ""


@dataclass(frozen=True)
class RebalanceResult:
    triggered: bool                 # drift exceeded threshold
    orders: List[OnchainOrder] = field(default_factory=list)
    submitted: bool = False         # a submitBasket tx was actually sent
    verified: bool = False          # drift fell below threshold afterwards
    tx: Optional[str] = None
    note: str = ""


@dataclass(frozen=True)
class KeeperReport:
    liquidity: LiquidityResult
    rebalance: RebalanceResult
    # Non-empty when the fail-closed gate refused to act this tick (the bot read
    # contradictory/missing state and skipped both jobs).
    blockers: tuple = ()


# ──────────────────────────────────────────────────────────────────────────
#  The bot
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class KeeperBot:
    """Single-tick keeper: read state, act, verify.

    Args:
        client: the live (or fake) vault client.
        target_weights: desired notional weight per coin.
        sz_decimals: size precision per coin (for rounding deltas).
        asset_ids: HIP-3 asset id per coin (for ``submitBasket`` encoding).
        side: basket direction (``"long"``/``"short"``).
        slippage: marketable-limit slippage cap for rebalance legs.
        config: liquidity buffer + drift thresholds.
        max_retries: verification re-reads before flagging an unverified action.
        dry_run: when True, plan and log but never transmit (default — safe).
    """

    client: KeeperClient
    target_weights: Dict[str, float]
    sz_decimals: Dict[str, int]
    asset_ids: Dict[str, int]
    side: str = "long"
    slippage: float = 0.005
    config: KeeperConfig = field(default_factory=KeeperConfig)
    max_retries: int = 3
    dry_run: bool = True

    def tick(self) -> KeeperReport:
        """Run both jobs once; liquidity first (redemptions are time-sensitive).

        A fail-closed gate runs first: if the reads are contradictory or missing,
        the bot refuses to act this tick and returns a report whose ``blockers``
        explain why — better to do nothing than bridge/trade on garbage state.
        """
        gate = evaluate_gate(
            KeeperState(
                idle=self.client.idle_assets(),
                pending_redeem=self.client.pending_redeem_assets(),
                nav=self.client.nav(),
                core_available=self.client.core_available(),
                positions=self.client.positions(),
                prices=self.client.prices(),
            )
        )
        if not gate.allowed:
            note = "gate blocked: " + "; ".join(gate.blockers)
            return KeeperReport(
                liquidity=LiquidityResult(
                    bridged=0.0, shortfall=0.0, submitted=False, verified=False, note=note
                ),
                rebalance=RebalanceResult(
                    triggered=False, submitted=False, verified=False, note=note
                ),
                blockers=gate.blockers,
            )

        return KeeperReport(
            liquidity=self._run_liquidity(),
            rebalance=self._run_rebalance(),
        )

    # ---- liquidity ----
    def _run_liquidity(self) -> LiquidityResult:
        idle = self.client.idle_assets()
        action = plan_liquidity(
            idle_assets=idle,
            pending_redeem_assets=self.client.pending_redeem_assets(),
            nav=self.client.nav(),
            core_available=self.client.core_available(),
            config=self.config,
        )
        if action.bridge_from_core <= 0:
            note = "idle buffer already covers redemptions"
            if action.shortfall > 0:
                note = f"nothing withdrawable on Core; shortfall {action.shortfall:.2f}"
            return LiquidityResult(
                bridged=0.0, shortfall=action.shortfall, submitted=False,
                verified=True, note=note,
            )

        if self.dry_run:
            return LiquidityResult(
                bridged=action.bridge_from_core, shortfall=action.shortfall,
                submitted=False, verified=False, note="dry-run: bridge planned, not sent",
            )

        tx = self.client.bridge_from_core(action.bridge_from_core)
        verified = self._verify_idle_rose(idle, action.bridge_from_core)
        return LiquidityResult(
            bridged=action.bridge_from_core, shortfall=action.shortfall,
            submitted=True, verified=verified, tx=tx,
            note="bridge confirmed" if verified else "bridge UNVERIFIED — alert",
        )

    def _verify_idle_rose(self, before: float, amount: float) -> bool:
        """Confirm idle USDC rose by most of the bridged amount (settles late)."""
        tolerance = 0.99 * amount
        for _ in range(self.max_retries):
            if self.client.idle_assets() - before >= tolerance:
                return True
        return False

    # ---- rebalance ----
    def _run_rebalance(self) -> RebalanceResult:
        positions = self.client.positions()
        prices = self.client.prices()
        current_weights = weights_from_positions(positions, prices)

        if not needs_rebalance(current_weights, self.target_weights, self.config):
            return RebalanceResult(triggered=False, verified=True, note="within drift threshold")

        total = gross_notional(positions, prices)
        targets = target_sizes_from_weights(self.target_weights, prices, total, self.side)
        deltas = compute_rebalance(targets, positions, self.sz_decimals)
        orders = rebalance_to_onchain(
            deltas, self.asset_ids, prices, self.sz_decimals, self.slippage
        )
        if not orders:
            return RebalanceResult(
                triggered=True, verified=True, note="drift flagged but deltas rounded to zero"
            )

        if self.dry_run:
            return RebalanceResult(
                triggered=True, orders=orders, submitted=False, verified=False,
                note="dry-run: rebalance planned, not sent",
            )

        tx = self.client.submit_basket(orders)
        verified = self._verify_drift_cleared()
        return RebalanceResult(
            triggered=True, orders=orders, submitted=True, verified=verified, tx=tx,
            note="rebalance confirmed" if verified else "rebalance UNVERIFIED — alert",
        )

    def _verify_drift_cleared(self) -> bool:
        """Re-read positions and confirm drift fell back under the threshold."""
        for _ in range(self.max_retries):
            w = weights_from_positions(self.client.positions(), self.client.prices())
            if not needs_rebalance(w, self.target_weights, self.config):
                return True
        return False


# ──────────────────────────────────────────────────────────────────────────
#  Loop runner
# ──────────────────────────────────────────────────────────────────────────
def run_loop(
    bot: KeeperBot,
    interval: float = 60.0,
    max_ticks: Optional[int] = None,
    sleep: Callable[[float], None] = time.sleep,
    on_report: Optional[Callable[[KeeperReport], None]] = None,
) -> List[KeeperReport]:
    """Run ``bot.tick()`` on a fixed interval.

    Sleeps ``interval`` seconds *between* ticks (never after the last one). With
    ``max_ticks=None`` it runs forever (production); pass an integer to bound it
    (tests/back-tests). ``sleep`` and ``on_report`` are injectable so the loop is
    testable without real time. Returns the reports collected (bounded runs).

    The bot defaults to dry-run; flip ``bot.dry_run = False`` to let it transmit.
    """
    if interval < 0:
        raise ValueError("interval must be >= 0")
    reports: List[KeeperReport] = []
    counter = range(max_ticks) if max_ticks is not None else itertools.count()
    for i in counter:
        try:
            report = bot.tick()
        except Exception as exc:
            # A read/encode/sign failure in a single tick must not kill the loop.
            # Turn it into an unhealthy report (non-empty blockers) so the health
            # snapshot + exit code surface it, then carry on: transient blips
            # self-heal next tick, a persistent fault stays loudly unhealthy.
            msg = f"tick raised: {type(exc).__name__}: {exc}"
            report = KeeperReport(
                liquidity=LiquidityResult(
                    bridged=0.0, shortfall=0.0, submitted=False, verified=False, note=msg
                ),
                rebalance=RebalanceResult(
                    triggered=False, submitted=False, verified=False, note=msg
                ),
                blockers=(msg,),
            )
        reports.append(report)
        if on_report is not None:
            on_report(report)
        is_last = max_ticks is not None and i == max_ticks - 1
        if not is_last:
            sleep(interval)
    return reports
