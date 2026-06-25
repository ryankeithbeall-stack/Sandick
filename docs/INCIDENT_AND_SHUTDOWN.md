# Aperture — Incident Response & Graded Shutdown

> **Status: unaudited, testnet only.** This runbook maps incident triggers to
> Aperture's *actual* on-chain and keeper levers. It assumes the architecture and
> roles in [`contracts/src/BasketVaultBase.sol`](../contracts/src/BasketVaultBase.sol),
> [`contracts/src/lib/HyperCoreActions.sol`](../contracts/src/lib/HyperCoreActions.sol),
> and the keeper ([`sandick/keeper_cli.py`](../sandick/keeper_cli.py)). Read those
> before acting. Related: [`GO-LIVE.md`](../GO-LIVE.md),
> [`docs/testnet-signoff.md`](testnet-signoff.md),
> [`docs/security/README.md`](security/README.md).

## What an operator can and cannot do

Aperture is a custody-free ERC-4626 vault that *is* its own HyperCore trading
account. **No role can move funds out** — withdraw/redeem/claim/`bridgeFromCore`
are the only exits and they are always pro-rata to share holders. Incident
response is therefore about *halting risk and guaranteeing exits*, never about
clawing funds back.

Two hard architectural limits shape every response below:

1. **No on-chain order cancel / force-close.** `HyperCoreActions` wires only
   limit order (action 1), USD-class transfer (7), and spot send (6). There is
   **no** cancel / scheduleCancel / modify / updateLeverage. CoreWriter is async
   and fire-and-forget — it never reverts on failure. **To flatten a position you
   submit reduce-only legs via `submitBasket`; you cannot cancel a resting order
   on-chain.**
2. **Flatten-via-reduce-only and full pause are mutually exclusive.**
   `submitBasket` is `whenNotPaused`. Once `pause()` is called the manager can no
   longer submit *any* orders, including the reduce-only legs that would flatten.
   Use **`reduceOnlyMode`** when you want the strategy wound down *while the
   manager keeps trading* (exits-only orders); use **`pause()`** only when you
   want trading fully stopped and are content to leave positions resting until
   `unpause()`.

## Roles and who can pull which lever

| Lever | Function | Authority |
|---|---|---|
| Soft freeze (wind-down) | `setReduceOnlyMode(true)` | guardian **or** owner |
| Tighten caps to ~0 | `setOrderCaps(...)` / `setAssetOrderCap(...)` | owner only |
| Emergency stop | `pause()` | guardian **or** owner |
| Restart | `unpause()` | owner only |
| Rotate strategy key | `setManager(newKey)` | owner only |
| Open redemption backstop | `setManagerTimeout(...)` (then let it elapse) | owner only |

The `guardian` is the fast key: it can `pause()` and `setReduceOnlyMode(true)` but
**cannot** unpause, change fees, rotate the manager, set caps, or move funds — so
it can live hot/automated while the owner stays cold. Guardian defaults to the
owner; set it via `setGuardian`.

## Triggers → graded response

| Trigger (how it surfaces) | First response |
|---|---|
| Keeper emits `blockers` / `UNVERIFIED` (`format_report_json` → `healthy:false`, nonzero exit) | (a) Watch, escalate to (b) if it persists |
| NAV read reverts / stale (`totalAssets()` / `_coreEquityUsd` read failure) | (b) Soft freeze; do not deposit-gate via NAV |
| Manager key suspected compromised | (c) `setManager(newKey)` immediately |
| Order-cap breach (`OrderNotionalExceeded` / `EpochNotionalExceeded`) | (b) Soft freeze + tighten caps |
| Mid-bridge NAV discontinuity (`_coreSpotUsd` gap, see below) | (a)/(b) — pause deposits, do not pause exits |
| Stuck redeem (queue not servicing, `redemptionDeficit() > 0`) | (b) then open backstop; never pause |

### (a) Watch — keeper re-reads, no on-chain action
The keeper fail-closed gate ([`keeper_guard.py`](../sandick/keeper_guard.py)) and
the `--health-out` snapshot already **refuse to act** on contradictory/missing
reads (negative NAV, idle > NAV, missing prices, pending redeems with zero NAV).
A single unhealthy tick means the keeper *did nothing*, which is the safe default.
Capture evidence (below), let it re-read next interval. Escalate only if blockers
persist or recur.

### (b) Soft freeze — wind down, keep exits open
`setReduceOnlyMode(true)`: the manager can now submit **only reduce-only legs**
(`ReduceOnlyRequired` reverts otherwise) and `bridgeToCore` is blocked — no new
margin flows to Core while exits stay fully open. Optionally tighten with
`setOrderCaps(small, small, epoch)` or per-asset `setAssetOrderCap` to throttle a
misbehaving/compromised manager's churn. The guardian can do the
`setReduceOnlyMode` part **fast** without the cold owner key.
> This is the lever to use when you still need the manager to *unwind* — full
> `pause()` would block the very reduce-only legs that flatten the book.

### (c) Manager compromise — rotate the key
`setManager(newKey)` (owner-only). The old key instantly loses
`submitBasket`/`bridgeToCore`/`bridgeFromCore`. Worst case before rotation is bad
trading bounded by the order caps — **the manager can never move funds out**.
`setManager` also resets `lastManagerAction`, giving the new key a full
`managerTimeout` window. If the old key is dumping risk, pair with (b) first
(guardian-fast) while you assemble the cold owner key.

### (d) Hard stop — halt trading, exits stay open by design
`pause()` (guardian or owner) halts deposits/mints and **all** manager trading
(`submitBasket`, `bridgeToCore`). It **never** halts exits: `withdraw`, `redeem`,
`requestRedeem`/`fulfillRedeem`/`claim`, and `bridgeFromCore` all stay open
(`_deposit` is `whenNotPaused`; the exit paths are not). Positions left open on
Core simply rest — remember you **cannot cancel them and cannot flatten while
paused**. To guarantee exits even if the manager also goes dark, ensure
`managerTimeout` is set so that after that window of manager silence **anyone**
can call `bridgeFromCoreForRedemptions(amount)` to pull USDC back from Core, up to
`redemptionDeficit()` (never more than is owed). A dark manager can delay but
never trap exits.

## Mid-bridge NAV discontinuity

`totalAssets() = _idleAssets() + _coreEquityUsd() + _coreSpotUsd()`.
`_coreSpotUsd()` **defaults to 0** — USDC parked in the Core spot account
mid-bridge is a known NAV gap (GO-LIVE §8, testnet-signoff §8). A transient NAV
dip during a bridge is expected, not an exploit, but it *can* misprice
deposits/fulfillments in that window. Response: prefer **(a)/(b)** — gate deposits
(`pause()` stops deposits while leaving exits open) rather than treating the dip
as insolvency. There is **no manual NAV oracle** to override; do not add one.

## Evidence capture (every incident, before and after acting)

```bash
# One read-only tick + machine-readable health snapshot (nothing transmitted):
python -m sandick.keeper_cli --once --health-out incident-<ts>.json
```

Record: the `health.json` (`blockers`, liquidity/rebalance notes), the affected
vault address, every governance tx hash (`setReduceOnlyMode` / `pause` /
`setManager` / `setOrderCaps`), `pricePerShare()`, `redemptionDeficit()`,
`totalPendingRedeemShares`, and `lastManagerAction`. **Never record secrets** —
the manager key is resolved only inside `keeper_cli` from `MANAGER_KEY`/
`HL_SECRET_KEY`; `keeper_chain` never reads env. Live broadcasts are additionally
hard-gated on `ALLOW_LIVE_TX=1` ([`safety.py`](../sandick/safety.py)); leaving it
unset is itself a freeze on any keeper-initiated transaction.

## Reopen criteria

Restart only when **all** hold:

1. **Positions reconciled** — Core positions match intent; any unwanted exposure
   flattened via reduce-only legs (only possible *before* re-pausing, or while in
   `reduceOnlyMode`, since `submitBasket` is paused under `pause()`).
2. **NAV sane** — `totalAssets()` reads cleanly, no stale/reverting precompile,
   no outstanding mid-bridge gap; `keeper_cli --once` returns `healthy:true`.
3. **Queue serviced** — `redemptionDeficit() == 0` (or actively draining via
   `bridgeFromCore` / the backstop) and claims are flowing.

Then the **owner** (not the guardian): `setReduceOnlyMode(false)` and/or
`unpause()`, restore `setOrderCaps` to normal, and re-export `ALLOW_LIVE_TX=1`
only on the deliberate live keeper runner.

## Per-vault vs platform scope

There is **no platform kill-switch over deployed vaults.** The
[`VaultFactory`](../contracts/src/VaultFactory.sol) stays each vault's
`protocolAdmin`, but that role governs *only the platform fee*
(`setVaultProtocolFee` / `setVaultProtocolAdmin`) — it **cannot** pause, set
reduce-only, rotate a manager, or touch funds on an already-deployed vault. Every
incident lever above is **per-vault**, exercised by that vault's own
owner/guardian. A platform-wide event must be handled vault-by-vault.
