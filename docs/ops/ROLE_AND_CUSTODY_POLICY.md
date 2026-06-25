# Aperture — Role & Custody Policy

> **Status: unaudited, testnet-only.** This document is operator/auditor-facing
> and describes the *deployed* authority model of the Aperture platform. It maps
> every privileged role to the real functions in `contracts/src/BasketVaultBase.sol`
> and `contracts/src/VaultFactory.sol`. Cross-refs:
> [`GO-LIVE.md`](../../GO-LIVE.md) · [`docs/testnet-signoff.md`](../testnet-signoff.md) ·
> [`KEY_ROTATION_POLICY.md`](KEY_ROTATION_POLICY.md) ·
> [`docs/security/README.md`](../security/README.md).

Aperture is a HIP-3 perp-basket vault **platform**: `VaultFactory.createVault`
deploys one `BasketVault` per operator. Each vault is an ERC-4626 share token that
*is itself* the HyperCore trading account — custody, trading, NAV and fee accrual
all live in the one contract. There is **no manual NAV oracle**: `totalAssets()`
is read live (`_idleAssets()` + `_coreEquityUsd()` + `_coreSpotUsd()`).

The on-chain authority model is deliberately small: OpenZeppelin `Ownable` +
three named roles. It is **not** an N-role RBAC system — there are exactly four
authorities below.

## Roles & custody class

| Role | Set by / on whom | Custody class | One-line scope |
|------|------------------|---------------|----------------|
| **Owner** (`owner()`, `Ownable`) | the vault creator (`createVault` passes `msg.sender`); rotated via `transferOwnership` | **Cold / multisig** | Vault governance: operator fees, manager, guardian, all caps, allow-list, unpause. Never a fund-exit path. |
| **Manager** (`manager`) | `owner` via `setManager` | **Hot, low-balance signer** | Trade-only: `submitBasket`, `bridgeToCore`, `bridgeFromCore`. Moves funds **only between the vault's own** EVM/Core balances — never out. |
| **Guardian** (`guardian`) | `owner` via `setGuardian` (defaults to `owner` at construction) | **Hot / automated allowed** | Fast emergency-stop only: `pause` and `setReduceOnlyMode`. Cannot unpause, set fees, rotate manager, or change caps. |
| **protocolAdmin** (= the `VaultFactory`) | stamped at construction to `address(factory)`; the factory's own `owner` is the platform | **Platform-controlled (cold)** | Governs **only** the platform fee: `setProtocolFeeConfig`, `setProtocolAdmin`. The vault `owner` can never zero the platform's cut. |

Per-vault isolation: every factory-deployed vault has **its own** `owner`,
`manager`, and `guardian` (the operator's keys). The **platform owner** is the
distinct `VaultFactory.owner()`, which drives platform-wide fee policy through the
factory's `setVaultProtocolFee` / `setVaultProtocolAdmin` (they call into the
vault *as* `protocolAdmin`). A vault operator is never the platform, and the
platform is never a vault operator.

## Authority matrix (real functions)

| Function (`BasketVaultBase` unless noted) | Owner | Manager | Guardian | protocolAdmin |
|---|:---:|:---:|:---:|:---:|
| `setFeeConfig` (operator fees) | ✅ | — | — | — |
| `setManager` / `setGuardian` | ✅ | — | — | — |
| `setManagerTimeout` | ✅ | — | — | — |
| `setOrderCaps` / `setAssetOrderCap` | ✅ | — | — | — |
| `setAllowedAsset` | ✅ | — | — | — |
| `setRequirePostOnly` | ✅ | — | — | — |
| `pause` | ✅ | — | ✅ | — |
| `setReduceOnlyMode` | ✅ | — | ✅ | — |
| `unpause` | ✅ | — | — | — |
| `submitBasket` / `bridgeToCore` / `bridgeFromCore` | — | ✅ | — | — |
| `setProtocolFeeConfig` / `setProtocolAdmin` | — | — | — | ✅ |
| `VaultFactory.createVault` | — anyone — | | | |
| `VaultFactory.setDefaultProtocolFee` / `setVaultProtocolFee` / `setVaultProtocolAdmin` | — *platform owner only* — | | | |
| `deposit` / `mint` (pausable) | — anyone (when not paused) — | | | |
| `withdraw` / `redeem` / `requestRedeem` / `fulfillRedeem` / `claim` / `bridgeFromCoreForRedemptions` | — anyone / pro-rata — | | | |

`fulfillRedeem` and `bridgeFromCoreForRedemptions` are permissionless by design:
once the manager is dark (`managerIsDark()` after `managerTimeout`), anyone may
bridge Core→EVM **up to `redemptionDeficit()`** to unblock exits — a dark manager
can delay but never trap redemptions.

## Fund-moving authority — Aperture's headline invariant

**No role has a fund-exit path. USDC leaves a vault only via the pro-rata
`withdraw` / `redeem` / `claim` paths, paid to share holders.**

- The contract custodies all USDC and is the HyperCore account; it acts only on
  its own behalf (CoreWriter semantics).
- `bridgeToCore` / `bridgeFromCore` (`BasketVault._bridgeToCore` /
  `_bridgeFromCore`) move USDC between the vault's **own** EVM and Core balances —
  the spot-send target is always `usdcSystemAddress`, never an arbitrary address.
- There is **no** `rescue`, `sweep`, or admin-transfer function on any role.
  Worst-case manager abuse is bad trading, bounded by `allowedAsset`,
  `maxOrderNotional` / `assetMaxOrderNotional`, the rolling `epochNotionalCap`,
  and (in wind-down) `reduceOnlyMode`.
- **All fees are dilution shares, not USDC transfers.** `_accrueFees` mints
  shares to `feeRecipient` (operator) and `protocolTreasury` (platform); the exit
  fee stays in the vault. Fee recipients exit like any other share holder.

This is asserted on-chain by the contract test **`manager has no path to extract
funds`** (`contracts/test/vault.test.js:552`), which checks the vault ABI exposes
**no** `rescue`/`sweep` and that the only fund-moving manager functions are
`bridgeToCore` / `bridgeFromCore`. The companion test **`guardian can pause +
de-risk but has no fund/fee/manager power`** (same file) pins the guardian's
boundary.

## Named human signoff-owners

This document defines *which key holds which authority*. The mapping of those
keys to **named humans / multisig signers** (owner multisig members, guardian
operator, manager on-call) and the production go-live attestation is deferred to
**`LAUNCH_SIGNOFF.md`** (the launch-attestation sibling doc). Until that exists,
the operational gate is [`docs/testnet-signoff.md`](../testnet-signoff.md). Do not
move owner/guardian to their production human signers until governance hardening
(owner → multisig/timelock) per [`GO-LIVE.md`](../../GO-LIVE.md) §🟢 is complete.
