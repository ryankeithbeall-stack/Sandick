# Aperture — Security Test Plan

> **Status: UNAUDITED, testnet-only.** This is the executable bridge from the
> [Threat Model](THREAT_MODEL.md) and [Risk Register](../RISK_REGISTER.md) to an
> audit: every load-bearing property maps to a **real, named test** that an
> auditor can re-run. The single unproven assumption — a *contract* account
> placing HIP-3 orders via CoreWriter on the live Trade.xyz dex — is **not**
> covered here; it is gated procedurally by [`GO-LIVE.md`](../../GO-LIVE.md) §2
> and [`docs/testnet-signoff.md`](../testnet-signoff.md) §6.

Aperture is a *platform*: `VaultFactory` deploys many `BasketVault` instances,
each an ERC-4626 vault that **is** its own HyperCore trading account (custody +
trade + NAV + fees in one contract). Tests live in two suites:

- **Contracts** — `contracts/test/vault.test.js` (in-process `@ethereumjs/vm`,
  runs the *compiled bytecode*). `MockBasketVault` exercises the accounting;
  the production `BasketVault` + `HyperCoreActions` wire path is covered by
  etching `MockCoreWriter` at the CoreWriter precompile address.
- **Off-chain** — `tests/*.py` (pytest, fully offline; the Hyperliquid SDK is
  faked in `conftest.py`).

## Property → test coverage map

| # | Property / threat (Risk ID) | Suite | Test(s) — verbatim |
|---|---|---|---|
| 1 | **No role can extract funds.** No `rescue`/`sweep`; only bridge in/out exists. | contracts | `manager has no path to extract funds` |
| 2 | **Manager is trade-only.** Non-manager cannot trade; rotation moves rights; manager cannot call any owner/guardian setter. (R-04) | contracts | `only manager can trade; non-manager reverts`; `owner can rotate the manager; trading rights follow`; `manager cannot call any owner/guardian-gated setter` |
| 3 | **Manager trades only allow-listed assets.** (R-04) | contracts | `manager can only trade allow-listed assets` |
| 4 | **Pause halts deposits + trading, never exits.** (invariant) | contracts | `pause blocks deposits and trading but never blocks exits` |
| 5 | **Guardian authority + negative-authority sweep.** Pause + reduce-only only; never unpause/fees/manager/caps/post-only; defaults to owner. | contracts | `guardian can pause + de-risk but has no fund/fee/manager power`; `guardian defaults to the owner; a random account cannot pause` |
| 6 | **Reduce-only wind-down.** Only shrinking legs trade; new margin blocked; exits stay open. (R-04) | contracts | `reduce-only mode: only shrinking legs trade; new margin blocked, exits open` |
| 7 | **Per-order + per-epoch + per-asset notional caps.** (R-04) | contracts | `per-order notional cap rejects oversized legs`; `per-epoch notional cap accumulates and resets`; `per-asset order cap overrides the global cap`; `requirePostOnly is owner-gated and stored` |
| 8 | **Async redeem queue; reserved ≤ idle; reserved excluded from NAV + sync withdrawals.** | contracts | `async redeem: request -> bridge -> fulfill (permissionless) -> claim`; `async redeem: cancel returns escrowed shares`; `reserved assets are protected from sync withdrawals`; `bridgeToCore preserves NAV; withdraw caps to idle liquidity` |
| 9 | **Manager-dark backstop, bounded by deficit.** Shut while active; opens on timeout; never over-pulls; heartbeat resets; owner-disableable. (R-05) | contracts | `redemption backstop is shut while the manager is active`; `redemption backstop opens after manager timeout; exit completes`; `redemption backstop can never pull more than is owed`; `manager activity resets the backstop countdown`; `owner can disable the redemption backstop` |
| 10 | **Live/trustless NAV = idle + clamped Core equity; underwater clamps to 0.** (R-02) | contracts | `HyperCoreReader returns accountValue (clamping negatives)`; `production BasketVault NAV = idle + reader equity` |
| 11 | **Donation/inflation resistance + share proportionality.** `_decimalsOffset()=6` virtual shares. (R-07) | contracts | `deposit mints shares and is redeemable 1:1 before PnL`; `two depositors get proportional shares`; `PnL on Core raises share price; redeemer collects gains` |
| 12 | **Fee monotonicity / HWM / caps.** Mgmt dilution; perf only above HWM, no double-charge; exit retained; caps + owner-only. (R-12) | contracts | `fee defaults are set at deployment`; `fee config is owner-only and capped`; `management fee accrues over time as dilution shares`; `performance fee charges only gains above the high-water mark`; `exit fee is retained in the vault for remaining holders` |
| 13 | **Platform fee is immutable to the operator; admin-only + capped.** (R-12) | contracts | `platform fee streams to the protocol treasury`; `operator and platform fees stack (separate recipients)`; `platform fee config is protocol-admin-only and capped`; `operator cannot touch the platform fee; the platform can` |
| 14 | **Factory wiring.** Stamps fee/treasury, stays `protocolAdmin`, enforces fee cap, tracks vaults; end-to-end platform fee. (R-10, R-12) | contracts | `factory creates a vault, records it, and wires the platform fee`; `factory tracks multiple vaults and enforces the fee cap`; `a factory-created vault charges the platform fee end-to-end` |
| 15 | **Recovery drill.** De-risk → tighten → rotate; old manager fully deauthorized. | contracts | `recovery drill: de-risk, tighten, rotate; old manager loses all power` |
| 16 | **CoreWriter wire bytes (MockCoreWriter recorder).** Exact action IDs + ABI for the production path. | contracts | `CoreWriter: bridgeToCore emits usdClassTransfer(spot->perp)`; `CoreWriter: submitBasket emits a limit order with the configured TIF`; `CoreWriter: requirePostOnly forces ALO (tif=1) on submitted orders`; `CoreWriter: bridgeFromCore emits usdClassTransfer THEN spotSend` |
| 17 | **Off-chain `ALLOW_LIVE_TX` broadcast gate.** Disabled by default; only `"1"` enables; gates every signed broadcast. (R-09) | python | `test_safety.py::test_disabled_by_default`, `::test_only_exact_one_enables`, `::test_enabled_when_set_to_one`; `test_keeper_chain.py::test_send_requires_allow_live_tx_env` |
| 18 | **Fail-closed keeper gate.** Refuses contradictory/missing reads; allows coherent + empty-vault states. (R-08) | python | `test_keeper_guard.py::test_coherent_state_is_allowed`, `::test_idle_exceeds_nav_blocks`, `::test_idle_positive_with_zero_nav_blocks`, `::test_negative_reads_block`, `::test_missing_price_for_position_blocks`, `::test_pending_redeem_with_zero_nav_blocks`; `test_keeper_bot.py::test_bot_refuses_to_act_on_contradictory_reads` |
| 19 | **Live NAV-read adapter correctness.** Idle nets reserved + floors; precompile decode/clamp; fail-safe on revert. (R-02) | python | `test_keeper_chain.py::test_idle_assets_nets_reserved`, `::test_idle_assets_floors_at_zero`, `::test_nav_scales_decimals`, `::test_core_available_clamps_when_fully_used`, `::test_core_available_zero_on_precompile_revert`, `::test_decode_margin_summary_roundtrip` |
| 20 | **Read-act-verify on silent CoreWriter failure.** Flags unverified actions when state doesn't move. (R-08) | python | `test_keeper_bot.py::test_liquidity_flags_unverified_when_idle_does_not_rise`, `::test_rebalance_unverified_when_drift_persists` |
| 21 | **Health snapshot exits nonzero when unhealthy** (cron/CI alerting). | python | `test_keeper_cli.py::test_health_out_exit_nonzero_when_unhealthy`, `::test_health_out_writes_snapshot_exit_zero_when_healthy`, `::test_format_report_json_unhealthy_on_blockers` |

## What an auditor should re-run

```bash
npm run test:contracts        # contracts/test/vault.test.js — 44 tests, all "ok"
npm run coverage:contracts    # gated: COVERAGE_MIN=85 total, COVERAGE_MIN_PER_FILE=80
pytest --cov=sandick           # ~205 tests; pyproject fail_under=90 enforces 90%
npm run slither               # informational (scripts/static_analysis.sh, --fail-none)
```

Gates are wired in `.github/workflows/ci.yml` (python matrix 3.10–3.12 + ruff;
contracts total/per-file coverage; informational Slither). The per-file contract
floor exists so a high total can't hide an untested production file — see
[`docs/security/README.md`](README.md).

## Focus areas for manual review

These are the places where a passing test suite is **necessary but not
sufficient** — the residual risk lives off-test:

1. **The live NAV-read path** (Risk **R-02/R-03**). Tests #10/#19 use a
   `MockMarginSummary` / canned precompile bytes; the *real* `accountMarginSummary`
   precompile (`0x…080F`, read by `HyperCoreReader`) is not exercised. Audit
   fresh-account revert behavior, start-of-block staleness, and that
   `_coreSpotUsd()` returning `0` (a known mid-bridge gap) cannot misprice a
   deposit/redeem. There is **no manual NAV oracle** — do not propose one.
2. **Fee accrual math** (Risk **R-12**). `_accrueFees()` runs before every
   value-changing action; tests #12/#13 assert HWM and no-double-charge within
   tolerances. Confirm the perf fee cannot mint phantom shares off a transiently
   inflated NAV (this couples to focus area 1 — see [`GO-LIVE.md`](../../GO-LIVE.md) §4).
3. **The redemption-backstop bound** (Risk **R-05**). Test #9 asserts
   `bridgeFromCoreForRedemptions` reverts at `redemptionDeficit() + 1`. Verify
   the deficit arithmetic (reserved/pending/idle) admits no path where a dark
   manager traps exits or a caller over-pulls Core funds.
4. **The no-cancel constraint.** `HyperCoreActions` wires limit order (1), USD
   class transfer (7), spot send (6) **only** — there is no on-chain cancel
   (no action 10/11), no `updateLeverage`. Test #16 pins the emitted bytes;
   confirm the only flatten path is reduce-only `submitBasket` legs, and that
   `reduceOnlyMode` is the intended wind-down lever.

## Cross-references

- [`docs/security/THREAT_MODEL.md`](THREAT_MODEL.md) — threats this plan covers
- [`docs/RISK_REGISTER.md`](../RISK_REGISTER.md) — R-IDs referenced above
- [`docs/security/README.md`](README.md) — Slither + coverage tooling
- [`GO-LIVE.md`](../../GO-LIVE.md) §2/§4 — the unproven order-placement assumption
- [`docs/testnet-signoff.md`](../testnet-signoff.md) — the on-chain sign-off the
  unit tests cannot stand in for
