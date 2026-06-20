"""Keeper decision logic for the SANDICK vault (pure, no network).

The keeper bot keeps the vault healthy between manual interventions. Its two
recurring jobs are:

1. **Liquidity** — maintain an idle-USDC buffer on HyperEVM and pull enough back
   from HyperCore to settle queued (async) redemptions, without over-draining the
   trading account.
2. **Rebalance** — detect when basket weights have drifted past a threshold and
   signal that delta orders should be submitted (the actual deltas come from
   ``rebalance.compute_rebalance``).

Everything here is pure arithmetic on already-read state, so it is fully
testable offline. The bot wiring (reading on-chain state, submitting bridges /
orders, retrying on silent CoreWriter failures) lives outside this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class KeeperConfig:
    """Tunables for the keeper's decisions.

    Args:
        buffer_fraction: idle-USDC buffer to hold on HyperEVM, as a fraction of
            NAV (e.g. 0.05 = keep ~5% liquid for redemptions/slippage).
        drift_threshold: max allowed absolute weight drift before a rebalance is
            signalled (e.g. 0.02 = 2 percentage points off target).
    """

    buffer_fraction: float = 0.05
    drift_threshold: float = 0.02

    def __post_init__(self) -> None:
        if not 0.0 <= self.buffer_fraction < 1.0:
            raise ValueError("buffer_fraction must be in [0, 1)")
        if self.drift_threshold < 0.0:
            raise ValueError("drift_threshold must be >= 0")


@dataclass(frozen=True)
class LiquidityAction:
    """How much USDC to bridge from Core to service redemptions + buffer."""

    bridge_from_core: float   # USDC to pull from HyperCore to HyperEVM
    idle_after: float         # projected idle USDC once the bridge settles
    shortfall: float          # unmet need if Core equity can't cover it


def plan_liquidity(
    idle_assets: float,
    pending_redeem_assets: float,
    nav: float,
    core_available: float,
    config: KeeperConfig = KeeperConfig(),
) -> LiquidityAction:
    """Decide how much USDC to bridge back from Core.

    Targets enough idle USDC to cover all queued redemptions plus a NAV-scaled
    buffer. Never asks for more than is available on Core, surfacing any
    remainder as ``shortfall`` (the manager must unwind positions first).

    Args:
        idle_assets: unreserved USDC currently idle on HyperEVM.
        pending_redeem_assets: USDC value of shares queued for redemption.
        nav: total vault NAV (for sizing the buffer).
        core_available: USDC realistically withdrawable from Core right now.
    """
    for name, val in (
        ("idle_assets", idle_assets),
        ("pending_redeem_assets", pending_redeem_assets),
        ("nav", nav),
        ("core_available", core_available),
    ):
        if val < 0:
            raise ValueError(f"{name} must be >= 0")

    buffer_target = nav * config.buffer_fraction
    need = pending_redeem_assets + buffer_target
    if idle_assets >= need:
        return LiquidityAction(bridge_from_core=0.0, idle_after=idle_assets, shortfall=0.0)

    deficit = need - idle_assets
    bridge = min(deficit, core_available)
    return LiquidityAction(
        bridge_from_core=bridge,
        idle_after=idle_assets + bridge,
        shortfall=deficit - bridge,
    )


def weights_from_positions(
    positions: Dict[str, float],
    prices: Dict[str, float],
) -> Dict[str, float]:
    """Notional weight per coin from signed positions and mark prices.

    Weights are computed on absolute notional (so a short still carries weight)
    and sum to 1 across non-zero legs. An empty/zero book returns all-zero.
    """
    notionals = {
        coin: abs(sz) * prices[coin]
        for coin, sz in positions.items()
    }
    total = sum(notionals.values())
    if total <= 0:
        return {coin: 0.0 for coin in positions}
    return {coin: ntl / total for coin, ntl in notionals.items()}


def max_weight_drift(
    current: Dict[str, float],
    target: Dict[str, float],
) -> float:
    """Largest absolute difference between current and target weights."""
    coins = set(current) | set(target)
    if not coins:
        return 0.0
    return max(abs(current.get(c, 0.0) - target.get(c, 0.0)) for c in coins)


def needs_rebalance(
    current: Dict[str, float],
    target: Dict[str, float],
    config: KeeperConfig = KeeperConfig(),
) -> bool:
    """True when weight drift exceeds the configured threshold."""
    return max_weight_drift(current, target) > config.drift_threshold
