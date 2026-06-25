# Aperture — Monitoring Policy

> **Status: unaudited, testnet only.** Signals and thresholds for operating a live
> `BasketVault` keeper. Aperture's NAV is read live from the chain (no manual
> oracle), so monitoring is **read-derived**, not receipt-derived.

## First principle: an EVM receipt is not Core success

CoreWriter is **asynchronous and fire-and-forget** — it returns nothing and does
**not** revert when a HyperCore action fails (insufficient margin, unfunded
account, bad asset). See [`contracts/src/lib/HyperCoreActions.sol`](../../contracts/src/lib/HyperCoreActions.sol).
A successful `submitBasket` / `bridge*` **transaction receipt only proves the
intent was queued**, never that the order filled or the bridge settled.

The keeper is built around this: [`sandick/keeper_bot.py`](../../sandick/keeper_bot.py)
runs **read → act → verify**, re-reading state after every action and flagging it
`UNVERIFIED` if the world did not change as expected (idle USDC did not rise by
the bridged amount; drift did not fall under threshold). **All alerting must key
off the re-read state, not the receipt.**

## How to run it

Run a single tick from cron/CI and alert on the exit code + snapshot:

```bash
sandick-keeper --once --health-out health.json   # exits NON-ZERO when unhealthy
```

`format_report_json` in [`sandick/keeper_cli.py`](../../sandick/keeper_cli.py)
sets `"healthy": false` when the fail-closed gate blocked, an action came back
`UNVERIFIED`, **or** there is an unmet redemption `shortfall` — and `main()`
returns a non-zero exit code in that case. Page on a non-zero exit; archive
`health.json` for the dashboard. Use `--execute` only on the deliberate live
runner, and only with `ALLOW_LIVE_TX=1` set ([`sandick/safety.py`](../../sandick/safety.py)).

---

## Signals & thresholds

### 1. Keeper gate blockers — fail-closed (P1)
`keeper_guard.evaluate_gate` ([`sandick/keeper_guard.py`](../../sandick/keeper_guard.py))
runs **before** any action and refuses the tick if reads are contradictory or
missing: negative `nav`/`idle`/`pending_redeem`/`core_available`; a position with
a missing price or an open position at a non-positive price; `idle > nav` (idle
is a *component* of NAV); or `pending_redeem > 0` while `nav == 0`.

- **Threshold:** any non-empty `blockers` → **page immediately**. The keeper is
  not acting (no bridges, no rebalances), so liveness work is stalled on bad reads.
- A brand-new empty vault legitimately has `nav == 0` with nothing to do — that
  alone is **not** a blocker.

### 2. UNVERIFIED actions (P1)
From `keeper_bot` read-act-verify: a `liquidity` or `rebalance` result that was
`submitted: true` but `verified: false` (note contains `UNVERIFIED`). Means the
EVM tx was sent but the Core effect did not show up within `max_retries` re-reads.

- **Threshold:** any `UNVERIFIED` → **page** (likely silent CoreWriter failure;
  candidate for the freeze step of [`RECOVERY_DRILL_RUNBOOK.md`](RECOVERY_DRILL_RUNBOOK.md)).

### 3. Redeem-queue shortfall / age (P1/P2)
`plan_liquidity` ([`sandick/keeper.py`](../../sandick/keeper.py)) surfaces a
`shortfall` when Core cannot cover queued redemptions + buffer. On-chain,
`redemptionDeficit()` is the authoritative gap (`owed - idle`).

- **Threshold (shortfall, P1):** `shortfall > 0` for more than one tick → page;
  the manager must unwind positions to free margin before exits can be fulfilled.
- **Threshold (age, P2):** oldest unfulfilled `requestRedeem` older than your SLA
  (suggest **> 24h**, well inside the 7-day `managerTimeout`) → investigate. If it
  approaches `managerTimeout`, the permissionless
  `bridgeFromCoreForRedemptions` backstop is about to be the only path — treat as
  a manager-liveness incident.

### 4. Idle buffer below the keeper liquidity target (P2)
The keeper targets `idle >= pending_redeem + buffer_fraction * nav`
(`buffer_fraction` default **0.05** = 5% of NAV, `KeeperConfig` in `keeper.py`).
`maxWithdraw`/`maxRedeem` are capped to idle liquidity, so a thin buffer means
synchronous exits get throttled into the queue.

- **Threshold:** idle below the target for 2+ consecutive ticks while
  `core_available > 0` (the keeper *should* be topping up) → investigate the
  bridge path. Note `core_available` is read straight from the
  `accountMarginSummary` precompile in [`sandick/keeper_chain.py`](../../sandick/keeper_chain.py),
  not from a receipt.

### 5. NAV drift / share-price discontinuity across a bridge (P2)
`pricePerShare() = totalAssets() / totalSupply()` must stay **continuous** through
a USDC↔Core bridge. The known gap: `_coreSpotUsd()` defaults to `0`, so USDC
parked in the Core *spot* account mid-bridge is momentarily dropped from NAV
(GO-LIVE.md §8 / testnet-signoff §8).

- **Threshold:** a step change in `pricePerShare` correlated with a `bridgeToCore`
  / `bridgeFromCore` that is **not** explained by realized PnL or fee accrual →
  investigate. A persistent dip is the `_coreSpotUsd` gap; a *jump* up at deposit
  time near a bridge is the dangerous case (transient NAV inflation → excess
  performance-fee shares — flagged for audit in GO-LIVE.md §4).

### 6. Drawdown vs high-water mark (P2)
`highWaterMark` is the highest net price-per-share ever reached; the performance
fee only charges gains above it. Track `pricePerShare()` against `highWaterMark`.

- **Threshold:** drawdown beyond your strategy's risk budget (suggest a tiered
  alert, e.g. **-10%** notify / **-20%** consider `setReduceOnlyMode(true)`). A
  near-zero or clamped `_coreEquityUsd` (underwater Core equity clamps to 0 in
  `HyperCoreReader`) is a hard stop → freeze and drill.

---

## Escalation

| Severity | Examples | Action |
|---|---|---|
| **P1** | gate blockers, `UNVERIFIED` action, `shortfall > 0` | Page on-call; begin [`RECOVERY_DRILL_RUNBOOK.md`](RECOVERY_DRILL_RUNBOOK.md) Step 1 (freeze). |
| **P2** | buffer below target, NAV/bridge discontinuity, drawdown notify, queue age | Investigate within SLA; escalate to P1 if it persists. |

All severities feed the incident process in
[`docs/INCIDENT_AND_SHUTDOWN.md`](../INCIDENT_AND_SHUTDOWN.md) for declaration,
comms, and post-mortem. The fast containment levers — `pause()` and
`setReduceOnlyMode(true)` — are guardian-callable, so the on-call keeper key can
brake without the cold owner key.

## Related

- Containment drill: [`RECOVERY_DRILL_RUNBOOK.md`](RECOVERY_DRILL_RUNBOOK.md)
- Live round-trip gate: [`docs/testnet-signoff.md`](../testnet-signoff.md)
- Remaining path to production: [`GO-LIVE.md`](../../GO-LIVE.md)
- Static-analysis baseline: [`docs/security/README.md`](../security/README.md)
