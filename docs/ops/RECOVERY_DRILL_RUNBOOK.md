# Aperture — Recovery Drill Runbook

> **Status: unaudited, testnet only.** This is a repeatable operator drill, not a
> mainnet procedure. Run it on a deployed `BasketVault` (HyperEVM testnet, chainid
> 998) and against the in-process contract suite. Every property the drill relies
> on is pinned to a passing test in [`contracts/test/vault.test.js`](../../contracts/test/vault.test.js),
> run via `npm run test:contracts` (the project has **no Foundry** — this is the
> in-process `@ethereumjs/vm` suite, see [`package.json`](../../package.json)).

## Why this drill exists

Aperture's `BasketVault` (concrete in [`contracts/src/BasketVault.sol`](../../contracts/src/BasketVault.sol),
logic in [`BasketVaultBase.sol`](../../contracts/src/BasketVaultBase.sol)) **is**
the HyperCore trading account: custody, trading, NAV, and fees live in one
ERC-4626 contract. No role can extract funds — the only way assets leave is
pro-rata `withdraw`/`redeem`/`claim`. That makes "recovery" narrow and concrete:
you are not recovering stolen funds (impossible by construction), you are

1. **stopping new risk** fast (guardian),
2. **proving** what the vault actually holds from live reads (no manual NAV oracle),
3. **draining Core back** to honor exits, and
4. **rotating a suspect manager key** so it can no longer trade.

Run the full drill **before each order-cap increase** (`setOrderCaps` /
`setAssetOrderCap`) — raising a cap widens the blast radius of a bad/compromised
manager, so re-prove the brakes first. Also run it after any change to
`managerTimeout`, `guardian`, or the keeper deployment.

A note that anchors the whole drill: **CoreWriter has no on-chain order CANCEL**
(see [`contracts/src/lib/HyperCoreActions.sol`](../../contracts/src/lib/HyperCoreActions.sol)
— it wires only limit order / USD class transfer / spot send, and is async +
fire-and-forget). You cannot pull a resting order on-chain. To flatten exposure
you submit **reduce-only** legs via `submitBasket`; `reduceOnlyMode` enforces that
the manager can only shrink the book.

---

## Step 0 — Pre-flight

```bash
npm run test:contracts        # 44 contract tests must be green before drilling
sandick-keeper --once --health-out health.json   # PREVIEW; nothing sent
```

Confirm `health.json` shows `"healthy": true` and no `blockers`. Note the current
`owner`, `guardian`, `manager`, `managerTimeout`, `maxOrderNotional`, and per-asset
caps so you can diff after the drill.

---

## Step 1 — Freeze (stop new risk)

Two independent levers, both callable from the **guardian** fast key OR the owner
(`onlyGuardianOrOwner`). Neither can move funds, change fees, or rotate the
manager — so the guardian can live hot while the owner stays cold.

| Lever | Effect | Auth |
|---|---|---|
| `pause()` | Halts deposits/mints + manager `submitBasket`/`bridgeToCore`. **Exits stay open** (`withdraw`/`redeem`/queue/`claim`/`bridgeFromCore`). | guardian or owner |
| `setReduceOnlyMode(true)` | Manager may submit **only reduce-only** legs; `bridgeToCore` blocked; exits stay open. Lets the manager *unwind* (a full pause would freeze that). | guardian or owner |

Choose `setReduceOnlyMode(true)` when you still need the manager to flatten the
book; choose `pause()` when you want the manager fully stopped. They compose.

- **Proving tests:**
  - `pause blocks deposits and trading but never blocks exits`
  - `reduce-only mode: only shrinking legs trade; new margin blocked, exits open`
  - `guardian can pause + de-risk but has no fund/fee/manager power`
  - `guardian defaults to the owner; a random account cannot pause`

---

## Step 2 — Verify exposure (read-derived, never receipt-derived)

NAV is **live and trustless**: `totalAssets() = idle USDC + _coreEquityUsd() +
_coreSpotUsd()`. `_coreEquityUsd` reads the `accountMarginSummary` precompile via
[`HyperCoreReader.sol`](../../contracts/src/HyperCoreReader.sol) and **clamps
underwater equity to 0**. There is **no manual NAV input** to reconcile or trust.
`_coreSpotUsd` defaults to `0` — a known mid-bridge gap (GO-LIVE.md §8).

```bash
sandick-keeper --once --health-out health.json   # PREVIEW: reads idle / NAV / core / positions
```

Read directly from the deployed vault and cross-check the keeper snapshot:
`totalAssets()`, `_idleAssets()` (idle = `balanceOf(vault) - reservedAssets`),
`totalPendingRedeemShares`, `redemptionDeficit()`, `managerIsDark()`.

- **Proving tests:**
  - `production BasketVault NAV = idle + reader equity`
  - `HyperCoreReader returns accountValue (clamping negatives)`
  - `bridgeToCore preserves NAV; withdraw caps to idle liquidity`

---

## Step 3 — Reconcile (redeem-queue deficit vs idle)

Exits larger than idle liquidity go through the async (ERC-7540-style) queue:
`requestRedeem` escrows shares, `fulfillRedeem` settles them **at fulfillment-time
price** (permissionless once idle funds exist), `claim` pays out. `reservedAssets`
is excluded from NAV — it belongs to claimers, not remaining holders.

Compute the gap: `redemptionDeficit() = owed - idle` (clamped at 0), where
`owed = convertToAssets(totalPendingRedeemShares)`. If the deficit is > 0, the
manager (or, when dark, the backstop in Step 4) must bridge USDC from Core before
queued redemptions can be fulfilled.

- **Proving tests:**
  - `async redeem: request -> bridge -> fulfill (permissionless) -> claim`
  - `reserved assets are protected from sync withdrawals`
  - `async redeem: cancel returns escrowed shares`

---

## Step 4 — Manager-dark drill (liveness backstop)

If the manager key goes dark, exits must still be honorable. After
`managerTimeout` seconds of manager silence (`lastManagerAction`), the
permissionless backstop opens: **anyone** may call
`bridgeFromCoreForRedemptions(amount)` — but only up to `redemptionDeficit()`. It
lands USDC in the vault's own idle balance (never moves funds out), never touches
Core beyond what is owed, and does **not** count as manager activity. A dark
manager can delay exits but never trap them.

Drill it in the suite by warping past the default 7-day timeout, then have a
non-manager account drain exactly the deficit and complete the exit:

```bash
npm run test:contracts   # exercises the warp -> backstop -> fulfill -> claim path
```

- **Proving tests (verified by name in `vault.test.js`):**
  - `redemption backstop is shut while the manager is active`
  - `redemption backstop opens after manager timeout; exit completes`
  - `redemption backstop can never pull more than is owed`
  - `manager activity resets the backstop countdown`
  - `owner can disable the redemption backstop`

---

## Step 5 — Rotate the manager key

`setManager(newManager)` is **owner-only** and cannot be set to the zero address.
Rotation moves all trading rights atomically: the new key can `submitBasket` /
`bridge*`; the **old key can no longer `submitBasket` or `bridgeFromCore`**.
`setManager` also resets `lastManagerAction` so the new manager gets a full
timeout window. The guardian/owner can keep `reduceOnlyMode` + tightened caps in
place across the rotation so the incoming key is boxed to wind-down only.

- **Proving tests:**
  - `recovery drill: de-risk, tighten, rotate; old manager loses all power`
    (de-risk → `setReduceOnlyMode(true)` → `setAssetOrderCap` → `setManager(bob)`;
    asserts the old manager's `submitBasket`/`bridgeFromCore` both revert and the
    new manager is confined to reduce-only legs within the tightened cap)
  - `owner can rotate the manager; trading rights follow`
  - `manager cannot call any owner/guardian-gated setter`

> The keeper's manager key is resolved from `MANAGER_KEY` / `HL_SECRET_KEY` by
> `keeper_cli` only; on rotation, repoint the keeper at the new key and restart.
> Every signed broadcast is also hard-gated on `ALLOW_LIVE_TX=1`
> ([`sandick/safety.py`](../../sandick/safety.py)) — keep it unset until the new
> key is confirmed.

---

## Step 6 — Restore

Reverse the freeze, owner-only and deliberate:

1. `setReduceOnlyMode(false)` — re-enable exposure-increasing legs + `bridgeToCore`
   (guardian or owner).
2. `unpause()` — re-open deposits + manager trading (**owner-only**; the guardian
   deliberately cannot restart).
3. Re-run `sandick-keeper --once --health-out health.json` and confirm
   `"healthy": true`, then resume the live keeper loop.

- **Proving test:** `pause blocks deposits and trading but never blocks exits`
  (the `unpause` tail re-opens deposits; `reduce-only mode …` covers the
  reduce-only toggle-off).

---

## Schedule & escalation

- **Before every cap increase** (`setOrderCaps` / `setAssetOrderCap`): run Steps 1–6.
- **After** any `setGuardian` / `setManagerTimeout` change, or a keeper redeploy.
- During a real incident, this runbook is the *containment* half; declaration,
  comms, and post-mortem follow [`docs/INCIDENT_AND_SHUTDOWN.md`](../INCIDENT_AND_SHUTDOWN.md).
- Live signals that should trigger a drill: see [`MONITORING_POLICY.md`](MONITORING_POLICY.md).

## Related

- Live round-trip gate: [`docs/testnet-signoff.md`](../testnet-signoff.md)
- Remaining path to production: [`GO-LIVE.md`](../../GO-LIVE.md)
- Static-analysis baseline: [`docs/security/README.md`](../security/README.md)
