# Aperture — Key Rotation Policy

> **Status: unaudited, testnet-only.** Operator/auditor-facing. Covers the four
> keys that touch an Aperture vault, their storage class, how to rotate each, and
> the secret-compromise runbook. Cross-refs:
> [`ROLE_AND_CUSTODY_POLICY.md`](ROLE_AND_CUSTODY_POLICY.md) (authority model) ·
> [`GO-LIVE.md`](../../GO-LIVE.md) · [`docs/testnet-signoff.md`](../testnet-signoff.md).

Grounded in `contracts/src/BasketVaultBase.sol` (`setManager`, `setGuardian`,
`transferOwnership`), `scripts/deploy.js` (deployer `PRIVATE_KEY`), and
`sandick/keeper_cli.py` (manager-key env resolution).

## Keys, storage class, and rotation

| Key | On-chain role | Storage class | Rotation mechanism |
|-----|---------------|---------------|--------------------|
| **Owner** | vault governance (`owner()`) | **Cold / multisig** (move to multisig/timelock before mainnet — GO-LIVE §🟢) | `transferOwnership(newOwner)` (OZ `Ownable`). |
| **Guardian** | fast emergency-stop | **Hot / automated OK** (pause + reduce-only only; can never move funds) | `owner` calls `setGuardian(newGuardian)`; `setGuardian(0)` disables it (owner becomes sole pauser). |
| **Manager** | trade-only strategy key | **Hot, low-balance signer** (only gas + the trade authority; never custody) | `owner` calls `setManager(newManager)`. |
| **Deployer** | `scripts/deploy.js` `PRIVATE_KEY` | **Release-only** (one-shot deploy; not retained in ops) | Not an on-chain role post-deploy — discard / rotate offline. See below. |

### Owner
Cold and, before mainnet, a multisig/timelock. The owner is the only key that can
unpause, set operator fees, rotate the manager/guardian, and set every cap/
allow-list. Rotate by `transferOwnership`. Because exits are never gated on the
owner, an owner key going dark cannot trap depositor funds — but it does freeze
governance, so treat owner-key loss as a high-severity incident.

### Guardian
Defaults to the `owner` at construction; delegate to a faster key via
`setGuardian` so emergency-stop can live on a hot/automated signer while the owner
stays cold. The guardian can **only** `pause` and `setReduceOnlyMode` — it cannot
move funds, change fees, rotate the manager, or `unpause` (owner-only). Rotating
or even losing the guardian key is low-severity: the owner can always
pause/unpause and reassign it.

### Manager
A hot, **low-balance** signer holding only the trade authority. Rotate by
`owner` → `setManager(newManager)`; this also resets `lastManagerAction` so the
new manager starts with a full window. **`managerTimeout` backstop protects exits
during the rotation gap:** while no manager is acting, once
`managerIsDark()` (silence > `managerTimeout`, default **7 days**) anyone may call
`bridgeFromCoreForRedemptions` up to `redemptionDeficit()` to service queued
redemptions. So a slow rotation, or a deliberately withheld key, can delay
trading but never trap exits.

#### Manager key resolution (operations detail)
The keeper resolves the manager key from the environment in
**`sandick/keeper_cli.py`** — it reads `MANAGER_KEY`, falling back to
`HL_SECRET_KEY` (`keeper_cli.main` → `_resolve(None, "MANAGER_KEY", "HL_SECRET_KEY")`).
The resolved key is then **passed into** `keeper_chain.Web3KeeperClient`, which
**never calls `os.getenv`/`os.environ` itself** — `keeper_chain.py` only receives
`private_key` as an argument. This keeps secret resolution in one place.

Two independent broadcast gates sit in front of any signed transaction:

- **`--execute`** (default is preview/dry-run; without it nothing is transmitted).
- **`ALLOW_LIVE_TX=1`** — a hard env kill-switch in `sandick/safety.py`
  (`require_tx_allowed`) checked immediately before *every* signed broadcast:
  `keeper_chain._send` (keeper bridge/submit, `keeper_chain.py:285`) and
  `execute.submit` (`execute.py:164`). Leave `ALLOW_LIVE_TX` **unset everywhere
  except the one deliberate live runner** — an accidental `--execute` with a key
  present still cannot transmit.

### Deployer (`PRIVATE_KEY`)
Used only by `scripts/deploy.js` to deploy `HyperCoreReader` + `VaultFactory` and
`createVault`. It is the vault `owner` transiently (to allow-list assets), then
`transferOwnership` hands the vault to `VAULT_OWNER`. After deploy it holds no
on-chain authority — treat it as release-only: do not reuse it as an ops key, and
rotate/retire it offline.

## Rotation triggers

Rotate the affected key(s) on any of:

- **Suspected or confirmed key compromise / leak** (see runbook below).
- **Personnel change** — anyone with access to the manager hot key or an owner
  multisig signer leaves.
- **Scheduled hygiene** — rotate the manager hot key periodically (it is the most
  exposed key).
- **Infrastructure migration** — moving the keeper host, CI secrets, or signer.
- **Governance hardening** — migrating the owner from an EOA to a multisig/timelock
  ([`GO-LIVE.md`](../../GO-LIVE.md) §🟢).
- **protocolAdmin migration** — platform moving fee governance to a new factory or
  multisig (`VaultFactory.setVaultProtocolAdmin`); this is a platform action, not
  a per-vault key rotation.

## Secret-incident runbook (manager key)

The manager key is the most likely incident because it is hot. A leaked manager
key cannot steal funds (see the custody invariant in
[`ROLE_AND_CUSTODY_POLICY.md`](ROLE_AND_CUSTODY_POLICY.md)) — worst case is hostile
trading within the allow-list/cap bounds — so the priority is **stop bad trades,
then rotate**.

1. **Stop the keeper.** Kill the live runner and unset `ALLOW_LIVE_TX` in its
   environment (the `safety.py` gate then fail-closes every broadcast).
2. **Brake on-chain.** Have the guardian/owner `pause` (halts `submitBasket` +
   `bridgeToCore`; exits stay open) and/or `setReduceOnlyMode(true)` so any
   surviving manager action can only shrink exposure. Note: a resting order
   **cannot be cancelled on-chain** (CoreWriter has no cancel) — flatten by
   submitting reduce-only legs, which is why reduce-only mode is the right brake.
3. **Rotate.** `owner` → `setManager(newManager)` with a fresh hot key. This
   resets `lastManagerAction`. (Exits stayed protected throughout by the
   `managerTimeout` backstop.)
4. **Scrub the secret.** Remove the leaked key from the keeper host env, CI
   secrets, and any `.env`. `.env`, `secrets.json`, and `prices.local.json` are
   **already in [`.gitignore`](../../.gitignore)** — confirm the key never reached
   git history; if it did, treat it as fully burned.
5. **Resume.** Re-provision `MANAGER_KEY` (or `HL_SECRET_KEY`) on the runner, set
   `ALLOW_LIVE_TX=1` only there, and restart the keeper in preview first
   (`--once`, no `--execute`) before going live.

For owner/guardian/protocolAdmin compromise, the on-chain rotation primitive is
`transferOwnership` / `setGuardian` / `VaultFactory.setVaultProtocolAdmin`
respectively; the same stop → rotate → scrub shape applies, executed from the
cold/multisig signer.
