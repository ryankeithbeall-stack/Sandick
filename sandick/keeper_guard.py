"""Fail-closed pre-action gate for the keeper (pure, no network).

The keeper reads vault + HyperCore state and then acts (bridge funds, submit a
rebalance). This module is the safety valve that runs FIRST: it derives a small
set of consistency checks over the reads and, if any fails, tells the bot to
*refuse to act* this tick rather than bridge/trade on top of contradictory or
missing state. Adapted from the Wren build's read-only ``accounting_source_of_truth``
gates (``stale``/``failed``/``contradictory`` reads -> block), reshaped to the
values Aperture's keeper already reads — Aperture's NAV comes live from the vault,
so there is no manual-oracle input to gate.

All checks are pure arithmetic on already-read values, so the gate is fully
unit-tested offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class KeeperState:
    """A single tick's reads, as the gate sees them."""

    idle: float                 # unreserved idle USDC on HyperEVM
    pending_redeem: float       # USDC value of queued (escrowed) redemptions
    nav: float                  # vault NAV (live)
    core_available: float       # free margin withdrawable from Core
    positions: Dict[str, float] # signed size per coin
    prices: Dict[str, float]    # mark price per coin


@dataclass(frozen=True)
class GateResult:
    """Outcome of :func:`evaluate_gate`. ``allowed`` is the bot's go/no-go."""

    allowed: bool
    blockers: Tuple[str, ...]


def evaluate_gate(state: KeeperState) -> GateResult:
    """Decide whether the keeper may act on this tick's reads.

    Blocks (refuses to act) when a read is corrupt or two reads contradict:

    * any of nav / idle / pending_redeem / core_available is negative;
    * a position has a missing price, or an OPEN position has a non-positive
      price (``weights_from_positions`` would divide against garbage);
    * idle USDC exceeds NAV (idle collateral is part of NAV — strictly greater
      is impossible, and ``idle>0`` with ``nav==0`` is a contradiction);
    * there are queued redemptions but NAV is zero (nothing to honor them with).

    ``nav == 0`` on its own is allowed: a brand-new/empty vault legitimately has
    zero NAV and nothing to do.
    """
    blockers: List[str] = []

    for name, val in (
        ("nav", state.nav),
        ("idle", state.idle),
        ("pending_redeem", state.pending_redeem),
        ("core_available", state.core_available),
    ):
        if val < 0:
            blockers.append(f"{name} is negative ({val})")

    for coin, sz in state.positions.items():
        px = state.prices.get(coin)
        if px is None:
            blockers.append(f"missing price for position {coin}")
        elif sz != 0 and px <= 0:
            blockers.append(f"non-positive price for open position {coin}")

    # Idle collateral is a component of NAV, so it can never exceed it.
    if state.idle > state.nav:
        blockers.append(f"idle ({state.idle}) exceeds NAV ({state.nav})")

    # Owed redemptions but no NAV to settle them with.
    if state.pending_redeem > 0 and state.nav == 0:
        blockers.append("pending redemptions but NAV is 0")

    return GateResult(allowed=not blockers, blockers=tuple(blockers))
