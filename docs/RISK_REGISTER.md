# Aperture — Risk Register

Authoritative, ID-stamped risk table for the Aperture HIP-3 perp-basket vault
platform. Each row cites the **real file/symbol** that mitigates it, so a fix can
be traced to code. Cross-referenced from [`GO-LIVE.md`](../GO-LIVE.md) (blockers
map to risk IDs), the [security tooling docs](security/README.md), the
[testnet sign-off runbook](testnet-signoff.md), the
[depositor guide](depositor-guide.md), and the
[risk disclosures](risk-disclosures.md).

**Status of the platform: UNAUDITED, testnet-only.** The single load-bearing,
unproven assumption is **R-01** — until it is proven on the live dex, every other
mitigation is conditional.

Severity = impact if it fires. Likelihood = chance of firing in the current
(testnet, unaudited) state. Status: `OPEN`, `MITIGATED` (control exists, residual
risk remains), `ACCEPTED` (known + tolerated for testnet), `BLOCKER` (must close
before mainnet deposits).

| ID | Risk | Severity | Likelihood | Current mitigation (file / symbol) | Owner | Status |
|----|------|----------|-----------|------------------------------------|-------|--------|
| **R-01** | **CoreWriter HIP-3 order placement is unproven.** A *contract* account may not be able to place HIP-3 orders via CoreWriter on the real Trade.xyz dex. If it can't, the on-chain trading design needs rework. | Critical | High | None on-chain — encoding is wired (`lib/HyperCoreActions.sol` `limitOrder`, action 1) but unverified against the live dex. Gated procedurally: [`GO-LIVE.md`](../GO-LIVE.md) §2 "Prove order placement" is a hard blocker; `docs/testnet-signoff.md` runbook. Placeholders (`tradexyz`, KIOXIA, `perpDexIndex`, `assetIds`, `coreScale`) are unverified. | Protocol eng | **BLOCKER** |
| **R-02** | **NAV mispricing via stale/underwater margin precompile.** `totalAssets()` reads HyperCore equity live; a stale or wrong read misprices deposits/redemptions. | High | Medium | `HyperCoreReader.accountEquityUsd` reverts (`MarginSummaryReadFailed`) on a failed/short read rather than returning a stale value, and clamps `accountValue <= 0` to `0` for share pricing. `_coreEquityUsd()` feeds `totalAssets()` (`BasketVaultBase.sol`). No manual NAV oracle by design. **Residual:** a revert DoS-es NAV/deposits; explicit stale-read handling is still a GO-LIVE §8 / §2 item (fresh-account behavior unverified). | Protocol eng | **OPEN** |
| **R-03** | **Mid-bridge spot USDC dropped from NAV.** USDC parked in the Core *spot* account mid-bridge is not counted, so share price dips/jumps across the multi-block bridge. | Medium | High | Known, intentional gap: `BasketVaultBase._coreSpotUsd()` defaults to `0` (documented in the hook). `BasketVault` does not override it yet. Closing it = wire the spot-balance precompile per [`GO-LIVE.md`](../GO-LIVE.md) §8. | Protocol eng | **ACCEPTED** (testnet) |
| **R-04** | **Compromised/rogue manager churns the book.** The trade-only `manager` key can submit destructive orders (bad trades, not theft — it can never move funds out). | High | Medium | Defense-in-depth caps in `BasketVaultBase`: per-leg `maxOrderNotional` + per-asset `assetMaxOrderNotional` (`setAssetOrderCap`), rolling `epochNotionalCap`/`epochLength` (`setOrderCaps`), `allowedAsset` allow-list (`setAllowedAsset`). Fast de-risk: `setReduceOnlyMode` (guardian-or-owner) forces reduce-only + blocks `bridgeToCore`; `pause()` (guardian-or-owner) halts `submitBasket`. Owner can `setManager` to rotate. | Operator | MITIGATED |
| **R-05** | **Manager goes dark — exits could starve.** No idle USDC and `bridgeFromCore` is manager-only, so queued redemptions can't be serviced. | High | Medium | `managerTimeout` (default 7 days, `setManagerTimeout`) + permissionless `bridgeFromCoreForRedemptions` backstop in `BasketVaultBase`: once `managerIsDark()`, anyone may bridge Core→EVM up to `redemptionDeficit()` only. A dark manager can delay exits but never trap them. | Operator | MITIGATED |
| **R-06** | **Single owner-key compromise.** The `owner` (Ownable) governs fees, manager, caps, guardian, asset list, unpause — one EOA is a single point of failure. | Critical | Medium | Role separation limits blast radius (owner cannot move funds out; trade authority is the separate `manager`; emergency-stop is the separate `guardian`). **No on-chain timelock/multisig yet.** Mitigation = move `owner` to a multisig/timelock before mainnet ([`GO-LIVE.md`](../GO-LIVE.md) §"Governance hardening"). | Operator / Gov | **OPEN** |
| **R-07** | **ERC-4626 first-depositor / donation inflation attack.** Attacker front-runs the first deposit and inflates share price via a direct token donation. | Medium | Low | `BasketVaultBase._decimalsOffset()` returns `6` (OZ virtual-shares mitigation), making the inflation economically infeasible. Reinforced by the seed/first-deposit step in the sign-off runbook. | Protocol eng | MITIGATED |
| **R-08** | **Silent CoreWriter failure.** CoreWriter is async + fire-and-forget and **never reverts** on failure (bad margin, unfunded account, invalid asset); there is **no on-chain order cancel** (no action 10/11) — flatten only via reduce-only `submitBasket` legs. An order may silently not fill. | High | Medium | Off-chain read-act-verify loop: `keeper_bot.py` verifies each action and tags unconfirmed ones `"... UNVERIFIED — alert"`; `keeper_guard.evaluate_gate` (fail-closed pre-tick gate, `KeeperState`/`GateResult.blockers`) refuses to act on contradictory/missing reads; `keeper_cli --health-out` exits nonzero when unhealthy (alert from cron). No on-chain enforcement (CoreWriter semantics) — see `lib/HyperCoreActions.sol` header. | Keeper ops | MITIGATED |
| **R-09** | **Accidental live broadcast.** A stray `--execute`/confirm with a key present transmits a real transaction. | High | Low | `safety.require_tx_allowed` hard `ALLOW_LIVE_TX=1` env gate on **every** signed broadcast: `keeper_chain._send` ("keeper bridge/submit") and `execute.submit` ("executor submit"). Only makes broadcasting harder, never weakens the on-chain no-extraction invariant. Manager key resolved by `keeper_cli` (from `MANAGER_KEY`/`HL_SECRET_KEY`); `keeper_chain` never calls `os.getenv`. | Keeper ops | MITIGATED |
| **R-10** | **Factory deploys an under-configured vault.** `VaultFactory.createVault` does not set order caps, asset allow-list, guardian, or fee recipient — a fresh vault opens with caps off (`maxOrderNotional`/`epochNotionalCap` = 0) and only constructor defaults. | High | Medium | Constructor sets safe defaults: `guardian = owner`, `managerTimeout = 7 days`, fees within `MAX_*` caps, manager/owner non-zero (`ZeroAddress`), `protocolFeeBps <= MAX_PROTOCOL_FEE_BPS`, `coreScale > 0`, `1 <= tif <= 3`. **But caps default off** — operator MUST call `setOrderCaps`/`setAssetOrderCap`/`setAllowedAsset` post-deploy ([`GO-LIVE.md`](../GO-LIVE.md) §1). No factory-level enforcement. | Operator | **OPEN** |
| **R-11** | **Legal / securities risk of a managed stock-perp basket.** A managed, fee-charging basket of equity-style perps (e.g. KIOXIA) may implicate securities/derivatives regulation across jurisdictions. | High | Medium | Out of scope for the contracts; addressed by access controls + disclosures only ([`docs/risk-disclosures.md`](risk-disclosures.md), optional deposit caps/whitelist in [`GO-LIVE.md`](../GO-LIVE.md) polish). Requires legal review before any non-testnet, public launch. | Founder / Legal | **OPEN** |
| **R-12** | **Operator over-extracts via fees / platform fee dispute.** A greedy operator maxes fees, or the operator/platform fee split is contested. | Medium | Low | Hard on-chain caps in `BasketVaultBase`: `MAX_MANAGEMENT_FEE_BPS` (5%/yr), `MAX_PERFORMANCE_FEE_BPS` (30%), `MAX_EXIT_FEE_BPS` (1%), `MAX_PROTOCOL_FEE_BPS` (2%/yr). Fees are dilution shares (no USDC leaves; the no-extraction invariant holds). Platform cut is `protocolAdmin`-governed (the factory) and the operator-`owner` cannot zero it (`setProtocolFeeConfig` is `onlyProtocolAdmin`). Performance fee uses the live NAV read — see R-02 and [`GO-LIVE.md`](../GO-LIVE.md) §4 audit focus. | Operator / Platform | MITIGATED |

## Notes on the invariant these risks live under

The contract's core guarantee is **no role can extract funds**: the only way USDC
leaves a `BasketVault` is pro-rata `withdraw`/`redeem`/`claim`/`bridgeFromCore`,
and exits are never pausable (`pause()` halts deposits + manager trading only).
Most "compromise" risks above therefore degrade to *bad trading or delay*, not
theft — which is why their severity/likelihood is bounded by the caps and the
redemption backstop rather than by perfect key hygiene.

## GO-LIVE → risk-ID map

| [`GO-LIVE.md`](../GO-LIVE.md) item | Risk IDs |
|---|---|
| §2 Prove order placement (load-bearing) | R-01 |
| §4 Security audit (NAV-read / fee path, bridging/`coreScale`) | R-02, R-12 |
| §8 Contract NAV completeness (`_coreSpotUsd`, stale-read handling) | R-02, R-03 |
| §6 Keeper live + monitoring/alerting | R-08, R-09 |
| §1 Deploy + post-deploy config (caps, allow-list) | R-10 |
| Governance hardening (owner → multisig/timelock) | R-06 |
| Real Trade.xyz data (replace placeholders) | R-01 |
| Disclosures / deposit caps / whitelist | R-11 |

> Review this register before any real-funds deployment. Add a row for every new
> trust assumption introduced by future code.
