# Aperture — Threat Model

> **Status: UNAUDITED, testnet-only.** This document models the system as built
> today. It is operator/auditor-facing and references real symbols. Treat the
> single load-bearing assumption — that a *contract* account can place HIP-3
> orders via CoreWriter on the Trade.xyz dex — as UNPROVEN until
> [`docs/testnet-signoff.md`](../testnet-signoff.md) §6 passes.
> See also: [`GO-LIVE.md`](../../GO-LIVE.md), [`README.md`](README.md) (Slither +
> coverage), [`contracts/README.md`](../../contracts/README.md) (trust model).

Aperture is a *platform*: `VaultFactory.createVault` deploys many independent
`BasketVault` instances, each an ERC-4626 vault that **is** its own HyperCore
trading account (custody + trade + NAV + fees in one contract). There is no
single shared vault and **no manual NAV oracle** — model accordingly.

---

## 1. Scope

In scope (HyperEVM, chainid 998 testnet):

| Component | File | What it does |
|---|---|---|
| Factory | `contracts/src/VaultFactory.sol` | deploys vaults, stamps the immutable protocol fee + treasury, stays each vault's `protocolAdmin` |
| Vault (abstract) | `contracts/src/BasketVaultBase.sol` | ERC-4626 + roles + NAV + async redeem queue + fees + caps |
| Vault (concrete) | `contracts/src/BasketVault.sol` | CoreWriter write path + reader NAV; holds immutables (`coreScale`, `usdcSystemAddress`, `tif`) |
| NAV reader | `contracts/src/HyperCoreReader.sol` | staticcalls the `accountMarginSummary` precompile (`0x..080F`) |
| Action encoder | `contracts/src/lib/HyperCoreActions.sol` | CoreWriter payloads: limit order (1), USD class transfer (7), spot send (6) |
| Keeper | `sandick/keeper*.py`, `safety.py`, `execute.py` | off-chain read-act-verify orchestrator (manager-key holder) |
| **Transacting frontend** | `frontend/chain.js` | viem wrapper that **signs and broadcasts** deposit/redeem/claim and admin txs from the user's injected wallet |

Out of scope but adjacent: HyperCore matching/liquidation engine and the
precompile implementations (we depend on them; we cannot change them); the
Trade.xyz dex listing itself.

The frontend is modeled honestly: `chain.js` is a **transacting** client. It
constructs `deposit`, `requestRedeem`, `redeem`, `claim`,
`bridgeFromCoreForRedemptions`, `createVault`, and (for admins) `submitBasket` /
`pause` calls and submits them via `window.ethereum`. A compromised frontend can
craft hostile calldata and trick a user into signing it — so it is part of the
attack surface, not a passive viewer.

## 2. Assets to protect

- **Pooled USDC custody** — every vault holds depositor USDC on HyperEVM plus
  margin on its own HyperCore account. Sole exit paths: `withdraw`/`redeem`,
  the async queue (`requestRedeem`→`fulfillRedeem`→`claim`), and
  `bridgeFromCore[ForRedemptions]` (Core→vault, not out).
- **Shares & escrowed claims** — transferable ERC-4626 shares; plus
  `pendingRedeemShares` (escrowed in the vault) and `claimableAssets` /
  `reservedAssets` (USDC earmarked for a specific redeemer, excluded from NAV).
- **`owner` key** (governance) — `setManager`, `setFeeConfig`, `setOrderCaps`,
  `setAssetOrderCap`, `setManagerTimeout`, `setAllowedAsset`,
  `setRequirePostOnly`, `setGuardian`, `unpause`.
- **`manager` key** (trade-only) — `submitBasket`, `bridgeToCore`,
  `bridgeFromCore`. Held by the keeper. Never moves funds out.
- **`guardian` key** (stop-only) — `pause` and `setReduceOnlyMode` only.
  Defaults to `owner`; designed to live on a hot/automated key.
- **`protocolAdmin` authority** (= the factory) — governs only the platform fee
  (`setProtocolFeeConfig`, `setProtocolAdmin`).
- **Displayed NAV / share price** — `totalAssets()` and `pricePerShare()` price
  deposits, redemptions, and the performance fee. Manipulation here mints value
  out of mispricing without ever touching custody directly.

## 3. Trust boundaries

1. **HyperEVM contract is authoritative for custody + shares.** Share balances,
   the redeem queue, `reservedAssets`, and fee accounting live here and are the
   source of truth. No off-chain component can alter them.
2. **HyperCore reads are evidence, not truth.** `_coreEquityUsd()` is a
   staticcall to a precompile that can **revert** (fresh/uninitialized account)
   or be **stale** (start-of-block read timing). On revert,
   `HyperCoreReader.accountEquityUsd` raises `MarginSummaryReadFailed`, so
   `totalAssets()` reverts — a *fail-loud*, not a silent zero. Underwater equity
   clamps to 0. `_coreSpotUsd()` defaults to 0 (known mid-bridge NAV gap).
3. **Manager is trade-only.** Enforced structurally: there is no manager-callable
   path that transfers `asset()` to an arbitrary address. Worst case = bad
   trading, capped by allow-list + order caps + reduce-only mode.
4. **Guardian is stop-only.** `onlyGuardianOrOwner` gates `pause` and
   `setReduceOnlyMode`; it cannot `unpause`, change fees, rotate the manager, set
   caps, or move funds. Safe to run hot.
5. **Owner is bounded-but-trusted.** Governance can degrade a vault (set fees to
   the hard caps, pause, dark-ify the manager) but **cannot seize deposits or
   block exits**: `pause()` never halts withdraw/redeem/claim/bridgeFromCore, and
   fee maxima are constants (`MAX_*_FEE_BPS`). Owner trust is bounded by code,
   not by reputation.
6. **Factory governs only the platform fee.** `protocolAdmin` can set
   `protocolFeeBps` (≤ `MAX_PROTOCOL_FEE_BPS` = 2%/yr) and migrate itself. It has
   no custody, trade, or pause authority over any vault.

## 4. Threats and controls (per component)

### NAV inflation → excess performance-fee shares  *(P0)*
A transiently inflated `totalAssets()` would (a) let a depositor mint cheap
shares or (b) push `pricePerShare()` above `highWaterMark` and mint perf-fee
shares to `feeRecipient` for unrealized/phantom gains.
- *Controls:* NAV is live and trustless (idle USDC + clamped Core equity); no
  writable oracle to poke. `_decimalsOffset()=6` virtual shares blunt
  inflation/donation attacks. `_accrueFees()` runs before every value-changing
  action so pricing is fee-correct.
- *Residual:* the precompile is an external read — a stale/laggy
  `accountMarginSummary` after a large fill, plus `_coreSpotUsd()==0` dropping
  in-flight USDC, can transiently misprice NAV. This is the audit focus in
  [`GO-LIVE.md`](../../GO-LIVE.md) §4/§8 and the explicit reason perf fees on an
  unverified NAV path are P0. Mitigate operationally by accruing/redeeming away
  from bridge windows until the spot precompile is wired.

### Manager order-cap evasion  *(P1)*
A compromised manager key churns the book or splits orders to slip caps.
- *Controls:* `submitBasket` enforces `allowedAsset`, per-leg
  `assetMaxOrderNotional` (falling back to global `maxOrderNotional`), and a
  rolling-epoch `epochNotionalCap`/`epochLength`. `reduceOnlyMode` forces every
  leg `reduceOnly` and blocks `bridgeToCore`. `requirePostOnly` forces ALO.
  None of these is a fund-exit path, so the worst case stays "bad trading."
- *Residual:* caps bound notional, not realized loss; a manager can still trade
  *poorly* within caps. Caps default to 0 (off) — the operator must set them.
  Note CoreWriter has **no on-chain cancel** (see below), so caps slow churn but
  can't recall a resting order.

### Redeem fulfillment vs. stale NAV  *(P1)*
`fulfillRedeem` prices escrowed shares at *current* `convertToAssets` at
fulfillment time. If NAV is momentarily wrong, a redeemer is over- or
under-paid at others' expense.
- *Controls:* pricing-at-fulfillment is deliberate (the redeemer, not remaining
  holders, bears moves while waiting). `_accrueFees()` runs first; the exit fee
  is retained in-vault, discouraging queue gaming. Fulfillment is
  **permissionless** once idle USDC exists, so the manager cannot selectively
  delay one redeemer. Reserved assets are excluded from NAV and from sync
  withdrawals.
- *Residual:* shares the same stale-NAV exposure as the perf-fee path; same
  mitigation. The off-chain `keeper_guard.evaluate_gate` refuses to act on
  contradictory reads (e.g. `idle > nav`, pending redemptions with `nav == 0`),
  a belt-and-braces layer.

### Factory hosting a malicious operator's vault under the brand  *(P2)*
`createVault` is **permissionless** — anyone becomes the `owner` of a new vault
that appears in `allVaults()` / the marketplace UI and inherits the Aperture
brand. A hostile operator can set max fees, run a bad strategy, or socially
engineer deposits.
- *Controls:* the platform-fee invariant holds regardless (operator can't zero
  the protocol cut); a hostile operator still **cannot seize deposits or block
  exits** — the same code-level guarantees apply to every vault. Fee maxima are
  enforced in the vault constructor and at the factory.
- *Residual:* brand/curation risk is **not** a contract control. The UI must not
  imply Aperture endorses an arbitrary listed vault; consider an allow-list or
  "unverified" badge in `frontend/app.js`. `chain.js.listVaults()` enumerates
  *all* factory vaults indiscriminately.

### Silent CoreWriter failure  *(P1)*
CoreWriter is async + fire-and-forget: `HyperCoreActions._send` never reverts on
failure (bad margin, invalid asset, unfunded account). A `submitBasket` /
`bridgeToCore` receipt is **not** proof of execution. Critically, there is **no
on-chain CANCEL** (no action 10/11), no `updateLeverage`, no scheduleCancel — to
flatten you submit reduce-only legs via `submitBasket`; a resting order cannot be
recalled on-chain.
- *Controls:* `chain.js` documents "CONFIRM by re-reading state — never treat a
  receipt as success." The keeper is read-act-**verify** and re-reads positions/
  NAV. `reduceOnlyMode` is the wind-down lever given the no-cancel constraint.
- *Residual:* between submit and the later Core block, intended risk state and
  actual state diverge; mispriced NAV (above) compounds this. Operationally,
  treat the vault as eventually-consistent and alert on drift (`--health-out`).

### Accidental broadcast  *(P1)*
A stray `--execute`, a key left in the environment, or a hostile frontend
auto-firing a manager/owner tx.
- *Controls (keeper):* `safety.require_tx_allowed` hard-gates **every** signed
  broadcast on `ALLOW_LIVE_TX=1` (in `keeper_chain._send` and `execute.submit`),
  on top of `--execute`/confirm. `keeper_guard` is fail-closed pre-tick. The
  manager key is resolved by `keeper_cli` from `MANAGER_KEY`/`HL_SECRET_KEY` and
  passed in; `keeper_chain` never calls `os.getenv`, so the key path is explicit.
  `keeper_cli --health-out` exits nonzero when unhealthy for cron/CI alerting.
- *Controls (frontend):* `chain.js` writes require a connected wallet
  (`_assertWallet`) and **every** state-changing call needs a user signature —
  there is no silent broadcast. Owner/manager actions are gated by `isOwner()` /
  `isManager()` in the UI, but those are convenience checks; the **contract**
  modifiers (`onlyOwner`/`onlyManager`/`onlyGuardianOrOwner`) are the real gate.
- *Residual:* a compromised frontend can still present hostile calldata for the
  user to sign (e.g. a max-value `approve`, or an admin signing a malicious
  `submitBasket`). Wallet-side review and least-privilege approvals are the
  mitigation; the contract caps the blast radius (no fund-exit path for manager).

## 5. Severity baseline

| Sev | Definition | Examples in this system |
|---|---|---|
| **P0** | Loss of funds; an unintended fund-exit path; or NAV that enables a mispriced deposit/redeem | a manager/owner path that moves `asset()` out; NAV inflation minting cheap shares or phantom perf-fee shares; a redeem priced off corrupt NAV |
| **P1** | Degraded safety or correctness without direct theft | order-cap evasion within "bad trading"; stale-NAV at fulfillment; silent CoreWriter failure / no-cancel divergence; accidental live broadcast |
| **P2** | Trust/governance/UX risk, no protocol-level loss | brand risk from permissionless `createVault`; owner degrading a vault to fee/risk maxima within hard caps |
| **P3** | Informational / hardening | Slither low-severity (`block.timestamp`, unindexed events — see [`README.md`](README.md)); single-owner key (move to multisig pre-mainnet, [`GO-LIVE.md`](../../GO-LIVE.md) "Pre-mainnet polish → Governance hardening") |

**Invariant that pins the P0 boundary:** *no role can extract funds; the only way
USDC leaves a vault is pro-rata withdraw/redeem/claim.* Any finding that breaks
this invariant — or that makes the price at which shares mint/burn diverge from
true NAV — is P0. Everything the manager, guardian, owner, and factory can do is
designed to stay strictly below that line.
