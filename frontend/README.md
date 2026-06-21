# Aperture — front end

A sleek, zero-build front end for **Aperture**, the vault deployment platform: a
marketplace of HIP-3 basket vaults, a "launch a vault" flow, and a full
depositor/manager deep-dive for the flagship **SANDICK** vault. Pure HTML/CSS/JS
— no bundler, no install. Open it or serve the folder.

> **Brand:** Aperture is the platform; SANDICK is the flagship vault, not the
> platform name.

```bash
# from the repo root
python -m http.server 8000 --directory frontend
# then open http://localhost:8000
```

(Or just open `frontend/index.html` directly in a browser.)

## What's here

| File              | Purpose                                                            |
|-------------------|-------------------------------------------------------------------|
| `index.html`      | Page structure: hero, vault marketplace, flagship (basket/calculator/depositor/admin), launch. |
| `styles.css`      | Dark DeFi theme; accent colors echo the SANDICK letter palette.   |
| `app.js`          | UI logic; demo state machine by default, live chain calls when enabled. |
| `config.js`       | Runtime config; `chain.enabled` gates live mode (off by default). |
| `chain.js`        | Optional viem layer over the deployed `BasketVault` (ES module). |
| `assets/sandick.png` | The SANDICK basket image, featured in the hero.                |

## Sections

- **Hero** — the platform pitch ("launch a basket vault, we power every one")
  plus platform-level stats (vault count, total TVL, platform fee).
- **Vaults** — the marketplace: a directory of vaults hosted on the platform,
  each card showing TVL, 30-day return, asset count, manager and the platform
  fee. SANDICK is pinned as the flagship / #1 performer; the others are demo
  entries. Data lives in the `VAULTS` array in `app.js`.
- **Flagship (SANDICK)** — the deep-dive for the flagship vault: the seven names
  (SanDisk, Arm Holdings, Nebius, Dell, Intel, CoreWeave, SK Hynix) whose logos
  spell **S A N D I C K**, each at 14.29%.
- **Calculator** — a live equal-weight planner. `allocate()` in `app.js` is a
  faithful port of `sandick.allocator.build_plan` (equal-weight branch):
  `gross_notional = capital × leverage`, sizes floored to each asset's
  `sz_decimals`. Editable capital, leverage, side and mark prices. With the
  example prices and $70,000 / 1× it reproduces the README's deployed margin
  ($69,997.39) and residual ($2.61) exactly. **Dry-run only — nothing is sent.**
- **Depositor app** — deposit USDC → SAND-LP shares at NAV/share, synchronous
  redeem, and the async redemption queue (`requestRedeem → pending → claimable
  → claim`) that models CoreWriter's delayed settlement.
- **Launch a vault** — a three-step explainer for deploying a new vault through
  the on-chain `VaultFactory` (pick basket → `createVault` → set fees & go live),
  with a facts card (standard, platform fee, operator caps, custody). In demo mode
  the button is a stub; in **live mode it calls `factory.createVault`** (see below).
- **Admin panel** — manager-gated controls for the flagship (discover / build /
  submit basket / rebalance / bridge) with an action log.

## Demo vs. live

By default (`chain.enabled = false`) the vault stats, balances, queue and admin
actions run on a **local demo state machine** — no chain calls. Data (`BASKET`,
`EXAMPLE_PRICES`) mirrors `config/sandick.basket.json` and
`config/prices.example.json`.

### Going live

`app.js` is wired to the chain through `chain.js` (a `viem` layer over the
deployed `BasketVault`). Flip `chain.enabled = true` in `config.js` and the
same buttons hit the contract instead of the demo state:

```js
// config.js
window.APERTURE_CONFIG = { chain: {
  enabled: true,
  chainId: 998,
  rpcUrl: 'https://rpc.hyperliquid-testnet.xyz/evm',
  factoryAddress: '0x…',  // deployed VaultFactory (powers the marketplace + launch)
  vaultAddress: '0x…',    // flagship BasketVault (SANDICK) — the detail view
  usdcAddress: '0x…',     // vault underlying (USDC)
  coreParams: {           // platform HyperCore immutables (from sandick.deploy_config)
    reader: '0x…', usdcSystemAddress: '0x…', usdcCoreTokenIndex: 0, coreScale: 1, tif: 3,
  },
}};
```

What's live when enabled:

- **Connect** — real wallet (`window.ethereum`); the button shows the address.
- **Marketplace** — with `factoryAddress` set, the **Vaults** grid is read from
  the chain: `factory.allVaults()` plus each vault's `totalAssets` (TVL),
  `totalSupply` (→ all-time return vs the genesis 1.0 share price), `name`,
  `symbol` and `manager`. The platform fee comes from `factory.protocolFeeBps()`.
  The vault matching `vaultAddress` is flagged as the flagship. (Without
  `factoryAddress` the grid stays in demo mode.)
- **Launch a vault** — the button calls `factory.createVault(asset, name, symbol,
  manager, coreParams)`; per-vault inputs are collected by prompt, the HyperCore
  immutables come from `coreParams`. On success the marketplace reloads and the
  new vault appears.
- **Depositor** — NAV/share + supply + your shares/USDC read from the flagship
  vault; **Deposit** runs `approve` (if needed) then `deposit`; **Redeem** runs
  the sync `redeem`; **Request redeem** runs `requestRedeem`; the queue renders
  the contract's `pendingRedeemShares` / `claimableAssets`; **Claim** runs `claim`.
- **Admin** — the panel unlocks only if the connected wallet is the vault
  `manager` or `owner`. **Bridge** prompts a direction/amount and runs
  `bridgeToCore` / `bridgeFromCore`. (Pause/allow-list helpers exist on
  `chain.js`.)

Still off-chain: **submit basket** and **rebalance** order *encoding* (HIP-3
asset ids + 1e8-scaled px/sz) is produced by the Python planner
(`sandick.onchain` / `exec_cli`); `chain.submitBasket(orders)` is the ready hook
to send that output.

Per-vault detail: the deposit/calculator/admin deep-dive is bound to the single
`vaultAddress` (the flagship), so non-flagship marketplace cards point back to it.
A full per-vault detail view (re-pointing `chain` at any selected vault) is the
next step — `chain._readAt(address, …)` already reads any vault by address.

It's **off by default** — testnet sign-off (chainid 998) isn't complete, so the
contract immutables it reads against must be verified first (see root `TODO.md`).
Because CoreWriter actions settle asynchronously and can fail silently, the live
handlers confirm by **re-reading state** after each tx, never by trusting a
receipt.
