# Aperture — Launch Sign-off

> **The human gate before real funds.** Aperture is **UNAUDITED, testnet-only**
> today. This one page is the checklist that must be fully green — every box, every
> named signer — before any **mainnet / real-fund** deposit is opened on any
> factory-deployed `BasketVault`. It governs the platform as a whole and each vault
> launched through `contracts/src/VaultFactory.sol`.
>
> Cross-refs: [`GO-LIVE.md`](../GO-LIVE.md) ·
> [`testnet-signoff.md`](testnet-signoff.md) · [`RISK_REGISTER.md`](RISK_REGISTER.md) ·
> [`ROLE_AND_CUSTODY_POLICY.md`](ops/ROLE_AND_CUSTODY_POLICY.md) ·
> [`INCIDENT_AND_SHUTDOWN.md`](INCIDENT_AND_SHUTDOWN.md) ·
> [`security/README.md`](security/README.md) ·
> [`depositor-guide.md`](depositor-guide.md) · [`risk-disclosures.md`](risk-disclosures.md).

## 1. Hard boundary

**No real-fund or mainnet deposit may be opened until every box below is checked
and every sign-off row is filled.** The frontend's `chain.enabled` and a mainnet
`vaultAddress` MUST NOT point at production funds while any box is `MISSING`. This
is a procedural gate, not a code one — nothing in the contracts blocks a premature
mainnet deploy, so the discipline lives here.

## 2. Named sign-off owners

Each owner must sign with a real name, date, and a link to the evidence (audit
report, testnet tx hashes, legal memo, ops runbook dry-run). Default is `MISSING`
— an empty cell blocks launch.

| Domain | Owner (name) | Date | Evidence link | Status |
|--------|--------------|------|---------------|:------:|
| **Security** (audit findings resolved; threat model + test plan reviewed) | _MISSING_ | — | — | ☐ |
| **Product-risk** (caps, eligibility, NAV/redemption behavior accepted) | _MISSING_ | — | — | ☐ |
| **Legal / Compliance** | _MISSING_ | — | — | ☐ |
| **Operations** (keys, monitoring, incident drill) | _MISSING_ | — | — | ☐ |

> **Legal note — this is a real securities question.** The flagship basket is a
> managed, equal-weighted set of **single-name stock perps** (the S-A-N-D-I-C-K
> names — SanDisk, Arm, Nebius, Dell, Intel, CoreWeave, Kioxia) wrapped in a
> tokenized, discretionarily-managed ERC-4626 share. A managed stock-derivative
> product sold to depositors can implicate securities / managed-fund / derivatives
> rules. Legal-Compliance sign-off must explicitly cover offering structure,
> eligible-user jurisdiction, and marketing — do not treat it as a formality.

## 3. Pre-funds technical gates

These mirror the [`GO-LIVE.md`](../GO-LIVE.md) blockers and the
[`docs/testnet-signoff.md`](testnet-signoff.md) runbook. All must pass on testnet
(chainid 998) first.

- [ ] **Prove CoreWriter HIP-3 order placement (THE load-bearing assumption).** A
      *contract* account places and fills an allow-listed leg on the real
      Trade.xyz dex via `submitBasket` → `HyperCoreActions.limitOrder` (action 1).
      Risk **R-01**; GO-LIVE §2, signoff §6. If it fails, stop — the on-chain
      trading design needs rework before anything else.
- [ ] **Confirm the unverified immutables.** `coreScale` (vault USDC is 6-dp),
      `usdcSystemAddress`, `usdcCoreTokenIndex`, `perpDexIndex`, and the per-coin
      `assetId`s — read back on-chain and matched to `config/deploy.json`
      (signoff §1, §4). These are the "UNVERIFIED inputs" the `BasketVault`
      constructor comments warn about.
- [ ] **Resolve real dex + symbols.** Replace placeholders `tradexyz` / `KIOXIA`
      with the live dex name and listed coin symbols (`python -m sandick.admin
      discover`); confirm each name is actually listed. GO-LIVE §5.
- [ ] **Full end-to-end round trip + NAV continuity.** deposit → `bridgeToCore` →
      `submitBasket` → NAV reflects equity → rebalance → `requestRedeem` →
      `bridgeFromCore` → `fulfillRedeem` → `claim`, with continuous share price
      across the multi-block bridge. Note the **known NAV gap**: `_coreSpotUsd()`
      returns `0`, so USDC parked mid-bridge is dropped from NAV until the
      spot-balance precompile is wired (Risk **R-03**, GO-LIVE §8).
- [ ] **Security audit complete, findings resolved.** Custody + share accounting +
      CoreWriter integration + redemption queue/backstop, with specific focus on
      the performance-fee NAV-read path (`_accrueFees` must not be transiently
      inflatable) and `coreScale`/decimal handling. GO-LIVE §4.

## 4. Eligibility & caps

| Item | Launch value | Notes |
|------|--------------|-------|
| Per-user deposit cap | _SET BEFORE LAUNCH_ | **Code gap.** |
| Total / TVL cap | _SET BEFORE LAUNCH_ | **Code gap.** |
| Eligible-user class | _SET BEFORE LAUNCH_ | E.g. allow-listed/whitelisted addresses only at launch. |
| Jurisdiction | _SET BEFORE LAUNCH_ | Excluded regions per Legal-Compliance sign-off (§2). |
| Support contact | _SET BEFORE LAUNCH_ | Published in the depositor guide / frontend. |
| Incident contact | _SET BEFORE LAUNCH_ | Routes to [`INCIDENT_AND_SHUTDOWN.md`](INCIDENT_AND_SHUTDOWN.md) responders. |

> **On-chain deposit/TVL caps are a code gap.** `BasketVaultBase` has **no**
> per-user or total deposit cap and no deposit whitelist today — `deposit`/`mint`
> are open to any address (only `whenNotPaused`). Gated/capped access at launch
> (GO-LIVE "Pre-mainnet polish") must be added in code or enforced at the frontend
> /allow-list layer; a frontend-only gate is bypassable on-chain and should be
> noted as residual risk.

## 5. Prohibited claims (governs `depositor-guide.md` + frontend copy)

Depositor-facing copy MUST NOT claim, imply, or omit the following. This governs
[`docs/depositor-guide.md`](depositor-guide.md) and all frontend strings.

- **No guaranteed liquidity / withdrawals.** Synchronous `redeem` is capped to
  *idle* USDC; larger exits go through the async queue and depend on the manager
  (or the `bridgeFromCoreForRedemptions` backstop) bridging funds back. Exits are
  never *trapped*, but they can be *delayed* — say so.
- **No guaranteed yield / returns.** The basket is leveraged perps; losses can
  approach total loss (see [`risk-disclosures.md`](risk-disclosures.md)).
- **No "safe" / "principal-protected" / "audited" language.** The product is
  **UNAUDITED** until §3's audit box is checked; copy must state this prominently.
- **No "instant" or "settled" on a green tx.** CoreWriter is async and
  fire-and-forget; a tx receipt does not mean the Core action landed. Copy must
  use pending → claimable framing.

## 6. Owner-key custody gate

- [ ] **Vault `owner` moved to a multisig / timelock before mainnet.** Today the
      `owner` (OpenZeppelin `Ownable`) is a single EOA that governs fees, manager,
      guardian, caps, allow-list, and `unpause` — one key is a single point of
      failure (Risk **R-06**, GO-LIVE "Governance hardening"). The owner can never
      move funds out, but it can mis-govern; rotate it to a multisig/timelock via
      `transferOwnership`. Keep the `manager` on a separate hot signer and the
      `guardian` on a fast key per
      [`ROLE_AND_CUSTODY_POLICY.md`](ops/ROLE_AND_CUSTODY_POLICY.md). For
      factory-governed platform fees, the `protocolAdmin`/`VaultFactory` owner key
      must be under the same custody discipline.

## 7. Security baseline (reference)

- [ ] **Slither static analysis** reviewed, no outstanding high/critical findings —
      [`docs/security/README.md`](security/README.md) (CI job `static-analysis`,
      `contracts/slither.config.json`, `scripts/static_analysis.sh`). Currently an
      informational `--fail-none` job; tighten to `--fail-high` once triaged.
- [ ] **Coverage gates green.** Contract line coverage in CI on both a total floor
      (`COVERAGE_MIN=85`) and a **per-file** floor (`COVERAGE_MIN_PER_FILE=80`);
      Python suite gated at `fail_under = 90` (`pyproject.toml`). Current suite:
      220 Python + 51 contract tests, plus a deterministic invariant/fuzz harness
      (`npm run test:invariant`) and an informational solhint lint
      (`.github/workflows/ci.yml`).
- [ ] **Security test plan + threat model reviewed** —
      [`docs/security/SECURITY_TEST_PLAN.md`](security/SECURITY_TEST_PLAN.md) and
      [`docs/security/THREAT_MODEL.md`](security/THREAT_MODEL.md), signed by the
      Security owner in §2.

---

**When, and only when, every box above is checked and every §2 row is signed:**
real-fund deposits may be enabled. Until then Aperture stays testnet-only.
