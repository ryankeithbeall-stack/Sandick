# SANDICK vault — depositor guide

> **Status: unaudited, testnet only.** Nothing here is investment advice. See
> [risk-disclosures.md](risk-disclosures.md) before depositing anything.

## What you're depositing into

SANDICK is a tokenized **ERC-4626 vault** on HyperEVM that holds an
**equal-weighted basket of seven perps** (SanDisk, Arm Holdings, Nebius, Dell,
Intel, CoreWeave, SK Hynix — the names whose logos spell **S A N D I C K**),
traded as Hyperliquid HIP-3 markets.

You deposit **USDC** and receive **SAND-LP shares**. Your shares are a pro-rata
claim on the vault's net asset value (NAV). As the basket gains or loses, the
**share price** (NAV ÷ shares) moves, and your shares are worth more or less —
you never need to manage positions yourself.

## The trust model (why the manager can't run off with your money)

- The **vault contract custodies all funds** and *is* the HyperCore trading
  account. The only way USDC leaves the contract is `withdraw` / `redeem` /
  `claim`, always paid pro-rata to share holders.
- The **manager** (strategy key) can *only* place trades on an allow-listed set
  of assets and bridge funds between the vault's own EVM and Core balances. It
  **cannot** transfer assets to itself or anyone else. Worst-case manager abuse
  is *bad trading*, not theft.
- New defense-in-depth controls: the owner can **pause** deposits and trading
  (exits always stay open), and the manager's order notional is bounded by
  **per-order and per-epoch caps**.

## Depositing

1. Connect a HyperEVM wallet and make sure you hold USDC.
2. Enter an amount and confirm. You receive `amount ÷ share_price` shares.
3. Shares are standard ERC-20 — transferable and composable.

## Redeeming — two paths

**Synchronous redeem** works when the vault has enough *idle* USDC on HyperEVM
to cover you immediately. `maxRedeem` / `maxWithdraw` show the cap.

**Async queue** (for redemptions larger than idle liquidity):

1. `requestRedeem(shares)` — your shares are escrowed in the vault.
2. The manager unwinds positions and bridges USDC back from Core over the next
   blocks. (You can `cancelRedeemRequest` while still pending.)
3. Once idle USDC exists, **anyone** can call `fulfillRedeem` for you — the
   manager cannot block your exit. Your shares are priced and burned **at
   fulfillment**, so you bear market moves until funds are actually set aside,
   not the remaining holders.
4. `claim()` pays out the reserved USDC whenever you like.

**If the manager goes dark.** Step 2 normally relies on the manager. As a
backstop, if the manager key is inactive for `managerTimeout` (default ~7 days),
**anyone** — including you — can call `bridgeFromCoreForRedemptions` to pull back
the USDC your queued exit is owed (capped to that amount), then `fulfillRedeem` +
`claim`. So an absent manager can delay your exit but can never trap it.

## Why the UI says "pending → claimable," not "done"

HyperCore actions (the bridge, the order fills) are **asynchronous and can fail
silently** — an EVM transaction receipt does **not** mean the Core action
landed. The front end therefore confirms state by **reading** the contract and
precompiles, and only shows a request as `claimable` once the USDC is actually
reserved. Never assume success from a green transaction alone.

## Reading NAV correctly

`totalAssets()` = idle USDC on EVM **+** perp equity on Core (margin + unrealized
PnL) **+** USDC parked in the Core spot account mid-bridge. Reserved (already
claimable) funds are excluded — they belong to exiting depositors, not NAV.
