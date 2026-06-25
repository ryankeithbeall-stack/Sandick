# SANDICK — Go-Live Checklist

What's left to take the vault from "built + tested offline" to **live**. Everything
in the codebase today is implemented and tested (205 Python + 44 contract tests,
all green); nothing below is blocked on more local engineering — it needs a live
chain, an audit, real Trade.xyz data, or ops wiring.

> Full history of what's already done lives in [`TODO.md`](TODO.md). This file is
> only the remaining path to production.

**Critical path:** **1 → 2**. If a contract account can place HIP-3 orders via
CoreWriter on the real dex (step 2), the rest is execution. If it can't, parts of
the on-chain trading design need rework — so prove that first, before investing
in everything else.

---

## 🔴 Blockers (no mainnet deposits until all of these are done)

### 1. Deploy to HyperEVM testnet (chainid 998)
- [ ] Get the real Trade.xyz data first (see step 5) — `deploy_config` needs it.
- [ ] `python -m sandick.deploy_config --dex-name <trade.xyz dex> --out config/deploy.json`
- [ ] `node scripts/deploy.js config/deploy.json --execute` (reader + vault, allow-list assets)
- [ ] Record the vault address; set fee recipient / order caps / manager timeout as desired.
- **Done when:** reader + vault are deployed and the basket assets are allow-listed.
- **Runbook:** [`docs/testnet-signoff.md`](docs/testnet-signoff.md)

### 2. Prove order placement — THE load-bearing assumption
- [ ] Seed the vault's HyperCore account; document fresh-account `accountMarginSummary` behaviour (revert vs zeros).
- [ ] Submit a single small allow-listed leg from the manager key and confirm it fills.
- **Done when:** an order placed by the *contract* shows up and fills on the Trade.xyz dex.
- **If it fails:** stop and revisit the on-chain trading design before anything else.

### 3. End-to-end testnet round trip
- [ ] deposit → bridgeToCore → submitBasket → NAV reflects equity → rebalance → requestRedeem → bridgeFromCore → fulfillRedeem → claim.
- [ ] Verify share price stays continuous across the multi-block bridges.
- [ ] Verify fee accrual (management/performance/exit) behaves on-chain as in tests.
- **Done when:** the full lifecycle works and NAV/share accounting stays consistent.

### 4. Security audit
- [ ] Full audit: custody + share accounting + CoreWriter integration + redemption queue + redemption backstop.
- [ ] Specific focus: the **performance-fee NAV-read path** (on-chain NAV must not be transiently inflatable → excess fee shares).
- [ ] Specific focus: bridging / `coreScale` / decimal handling.
- **Done when:** audit complete and findings resolved.

---

## 🟠 Required for a real launch

### 5. Real Trade.xyz data (replace placeholders)
- [ ] Resolve the real dex-name string + per-coin perp symbols + asset IDs + `sz_decimals` from the live `meta` (`python -m sandick.admin discover`). **Do not hardcode guesses.**
- [ ] Confirm which of the seven names are actually listed; substitute any that aren't while keeping the SANDICK spelling. (The former at-risk K slot, SK Hynix, has been swapped for **Kioxia**, which is now listed on Hyperliquid.)
- [ ] Confirm the vault's EVM USDC is 6-decimal (else fix `coreScale`).
- **Note:** blocked in the dev sandbox (`api.hyperliquid.xyz` not allowlisted); run from a host with egress.
- **Done when:** `config/sandick.basket.json` + `config/deploy.json` hold real, verified strings.

### 6. Run the keeper against a live node
- [ ] `pip install -e ".[keeper,live]"`; run `sandick-keeper --once` in **preview** first.
- [ ] Verify `HyperliquidMarketData.positions()` parses the HIP-3 dex payload (the one untested line).
- [ ] Flip to `--execute` only after step 2 passes — **and** export `ALLOW_LIVE_TX=1`. Every signed broadcast (keeper bridge/submit and `exec_cli` orders) is hard-gated on that env var on top of `--execute`/`confirm`, so an accidental `--execute` with a key present can never transmit. Leave it unset everywhere except the deliberate live runner.
- [ ] Add monitoring + alerting (NAV drift, failed actions, low idle buffer, drawdown). The keeper now emits a fail-closed gate (`blockers`) and a machine-readable health snapshot — run `sandick-keeper --once --health-out health.json` (exits non-zero when unhealthy) from cron/CI and alert on it.
- **Done when:** the keeper services liquidity + rebalances on testnet and alerts on misses.

### 7. Go live on the frontend
- [ ] Fill `vaultAddress` / `usdcAddress` / `rpcUrl` in `frontend/config.js`; set `chain.enabled = true`.
- [ ] Smoke-test the depositor flow (connect → deposit → redeem/queue → claim) against testnet.
- [ ] (Optional) wire the admin submit/rebalance encoding into the browser, or keep driving it from the Python planner / keeper.
- **Done when:** the live UI completes a deposit→redeem cycle against the deployed vault.

### 8. Contract NAV completeness
- [x] Wire the real spot-balance precompile into `_coreSpotUsd()` so USDC parked
      mid-bridge counts toward NAV. **Done in code:** `HyperCoreReader.spotBalanceUsd`
      reads the `0x..0801` precompile and scales spot-wei (8dp) down to the asset's
      6dp; `BasketVault` overrides the hook. **Still must be verified on testnet** —
      see the reader's MUST-VERIFY notes (precompile address, input ABI order
      `(address,uint64)`, USDC `weiDecimals`, and uninitialized-account
      revert-vs-zeros, which would brick `totalAssets()` if it reverts).
- [ ] **Verify the USDC↔Core bridge decimal convention.** `usdClassTransfer`
      (perp ntl) and `spotSend` (spot wei) may use *different* decimals, yet
      `BasketVault` passes the same `coreScale`d amount to both. The new spot
      reader makes any mis-scaling visible in NAV, so confirm the bridged amounts
      are correct end-to-end before trusting mid-bridge NAV continuity.
- [x] Failed/short precompile reads now **revert** (`MarginSummaryReadFailed` /
      `SpotBalanceReadFailed`) instead of returning a stale/zero value that could
      misprice deposits. NAV-manipulation resistance on the performance-fee path
      stays an audit focus (step 4).
- **Done when:** NAV is continuous through a bridge and reads can't misprice
  deposits or fee shares. (Best done during step 3.)

---

## 🟢 Pre-mainnet polish (optional)

- [ ] Foundry test suite mirroring the ethereumjs tests, + `forge fmt` / solhint (auditor familiarity).
- [ ] Fork/integration tests against a HyperEVM testnet fork.
- [x] Deposit cap — owner-settable TVL cap (`setDepositCap`, 0 = uncapped),
      enforced in `maxDeposit`/`maxMint` and a hard `_deposit` backstop; never
      blocks exits or fee/PnL NAV growth. (Per-address whitelist still optional.)
- [ ] Decide final fee parameters + treasury address (defaults: 2% mgmt / 10% perf / 0.1% exit, recipient = owner).
- [ ] Governance hardening: move owner to a multisig / timelock before mainnet.

---

## Reference
- Sign-off runbook: [`docs/testnet-signoff.md`](docs/testnet-signoff.md)
- Contracts overview + trust model: [`contracts/README.md`](contracts/README.md)
- Depositor explainer + risk: [`docs/depositor-guide.md`](docs/depositor-guide.md), [`docs/risk-disclosures.md`](docs/risk-disclosures.md)
- Full done/pending history: [`TODO.md`](TODO.md)
