# Sandick — Outstanding TODO

Status of the HIP-3 equal-weighted basket vault. What's **done** is tested
(139 Python + 16 contract tests); what's below is what remains before this can
hold real money, plus the product surface that doesn't exist yet.

Legend: 🔴 blocker for mainnet · 🟠 important · 🟢 nice-to-have

---

## 1. Front-end UI — SCAFFOLDED (demo) 🟠
A sleek, zero-build front end now lives in [`frontend/`](frontend/) (hero with
the SANDICK image, basket overview, live equal-weight calculator, depositor app
and admin panel). It runs on a **local demo state machine** — no chain calls
yet. Remaining to make it live:

- [x] **Depositor app** UI: connect wallet, deposit USDC, view shares,
      NAV/share price, basket breakdown, redeem (sync) and the async queue
      (`requestRedeem` → status → `claim`). *(demo state — needs wiring)*
- [x] **Admin panel** UI: discover HIP-3 assets, build/adjust the basket,
      preview the equal-weight plan, submit basket, rebalance, bridge to/from
      Core. Gated to the manager/owner address. *(demo state — needs wiring)*
- [x] **Async-aware UX**: queue models "pending → claimable" on a delay rather
      than trusting an EVM receipt.
- [ ] **Wire to chain**: replace demo handlers with wagmi/viem calls; read NAV /
      positions / queue state from the contract + read precompiles.
- [x] Stack decision (zero-build vanilla today; React + wagmi/viem on HyperEVM
      for the live wiring).

## 2. Testnet sign-off (chainid 998) 🔴
The architecture is simulation-tested only. Prove the live round-trip:

- [ ] Deploy via `sandick.deploy_config` + `scripts/deploy.js` to testnet.
- [ ] **Seed the vault's Core account** before opening deposits (fresh-account
      `accountMarginSummary` behavior must be confirmed — revert vs zeros).
- [ ] Confirm a **contract account can place HIP-3 orders via CoreWriter** on the
      Trade.xyz dex (the one medium-confidence assumption).
- [ ] Verify the calibrated immutables on-chain: `perpDexIndex`, each HIP-3
      `assetId`, USDC **system address** + token index, and **`coreScale`**
      (EVM↔Core decimals from `spotMeta`).
- [ ] End-to-end: deposit → bridgeToCore → submitBasket → NAV reflects equity →
      rebalance → requestRedeem → bridgeFromCore → fulfill → claim.
- [ ] Confirm the bridging flow (USDC EVM→spot→perp and back) and the
      start-of-block read timing against deposits/rebalances.

## 3. Security audit 🔴
- [ ] Full audit before any mainnet deposits (custody + share accounting +
      CoreWriter integration + redemption queue).
- [ ] Consider an independent review of the NAV-pricing path specifically
      (share price = on-chain reads; any manipulation = mispriced deposits).

## 4. Contract hardening 🟠
- [~] **NAV completeness**: `_coreSpotUsd()` hook is now folded into
      `totalAssets` (default 0); wiring the real spot-balance precompile so USDC
      parked in spot mid-bridge is counted remains.
- [x] **Pausability / circuit breaker** (owner pauses deposits/trading; exits
      stay open). `pause`/`unpause` in `SandickVaultBase`.
- [x] **Per-tx and per-epoch caps** on manager order notional (`setOrderCaps`).
- [ ] Decide whether `bridgeFromCore` should be partially permissionless to
      guarantee redemption liveness if the manager goes dark.
- [~] Handle negative/under-water `accountValue` (reader clamps to 0); **stale
      reads** still need explicit handling.
- [x] Events/telemetry for every state transition (subgraph-friendly) — incl.
      `OrderCapsUpdated` + OZ `Paused`/`Unpaused`.

## 5. Operations / keeper 🟠
- [~] **Manager keeper bot**: pure decision logic landed in `sandick/keeper.py`
      (idle-buffer / bridge-back sizing + drift-based rebalance signal, tested);
      the bot wiring (reads, submits, retries) remains.
- [ ] **Verification reads**: after each CoreWriter submit, confirm fills via
      read precompiles / API (silent-failure handling), retry/alert on misses.
- [ ] Monitoring + alerting (NAV drift, failed actions, low buffer, drawdown).

## 6. Config / data 🟠
- [ ] Replace placeholder coin symbols (e.g. `KIOXIA`) and the `tradexyz` dex
      name with the **real Trade.xyz dex name + symbols** once known.
- [ ] Confirm the vault's EVM USDC token is 6-decimal (else adjust `coreScale`).

## 7. Engineering hygiene 🟢
- [x] CI: `ruff` + `pytest` (coverage-gated, py3.10–3.12) + `npm run
      test:contracts` + contract coverage on every push (`.github/workflows/ci.yml`).
- [ ] Foundry test suite (mirrors the ethereumjs tests) for auditor familiarity.
- [ ] Fork/integration tests against a HyperEVM testnet fork if tooling allows.
- [x] Linting/formatting for Python (ruff, configured in `pyproject.toml`).
- [ ] Solidity formatting/linting (forge fmt / solhint).

## 8. Product / economics 🟢
- [ ] Fee model (management/performance) if desired — currently none on-chain.
- [ ] Deposit caps / whitelisting if access control is wanted later.
- [x] Docs: depositor-facing explainer + risk disclosures (`docs/`).

---

### Done (for reference)
Discovery · basket builder (equal/weighted/grouped) · planner + dry-run · saved
plan artifact · off-chain execution CLI (`verify`/`run`) · on-chain ERC-4626
vault (custody, trade-only manager + allow-list, NAV via `accountMarginSummary`
precompile, async redemption queue) · rebalance (delta, reduce-only) · HIP-3
asset-id / 1e8 encoding bridge · deploy + calibration scripts · owner pause +
order-notional caps · spot-NAV hook · keeper decision logic · front-end
(demo) + viem chain layer · CI + ruff + coverage gates · depositor docs. All
tested (139 Python + 16 contract).
