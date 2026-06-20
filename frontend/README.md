# SANDICK — front end

A sleek, zero-build front end for the SANDICK HIP-3 equal-weighted basket vault.
Pure HTML/CSS/JS — no bundler, no install. Open it or serve the folder.

```bash
# from the repo root
python -m http.server 8000 --directory frontend
# then open http://localhost:8000
```

(Or just open `frontend/index.html` directly in a browser.)

## What's here

| File              | Purpose                                                            |
|-------------------|-------------------------------------------------------------------|
| `index.html`      | Page structure: hero, basket, calculator, depositor app, admin.   |
| `styles.css`      | Dark DeFi theme; accent colors echo the SANDICK letter palette.   |
| `app.js`          | Demo logic (see below).                                            |
| `config.js`       | Runtime config; `chain.enabled` gates live mode (off by default). |
| `chain.js`        | Optional viem layer over the deployed `SandickVault` (ES module). |
| `assets/sandick.png` | The SANDICK basket image, featured in the hero.                |

## Sections

- **Hero** — the SANDICK image plus the one-line pitch and headline vault stats.
- **Basket** — the seven names (SanDisk, Astera Labs, Nebius, Dell, Intel,
  CoreWeave, Kioxia) whose logos spell **S A N D I C K**, each at 14.29%.
- **Calculator** — a live equal-weight planner. `allocate()` in `app.js` is a
  faithful port of `sandick.allocator.build_plan` (equal-weight branch):
  `gross_notional = capital × leverage`, sizes floored to each asset's
  `sz_decimals`. Editable capital, leverage, side and mark prices. With the
  example prices and $70,000 / 1× it reproduces the README's deployed margin
  ($69,997.39) and residual ($2.61) exactly. **Dry-run only — nothing is sent.**
- **Depositor app** — deposit USDC → SAND-LP shares at NAV/share, synchronous
  redeem, and the async redemption queue (`requestRedeem → pending → claimable
  → claim`) that models CoreWriter's delayed settlement.
- **Admin panel** — manager-gated controls (discover / build / submit basket /
  rebalance / bridge) with an action log.

## Demo vs. live

The vault stats, balances, queue and admin actions are a **local demo state
machine** — there are no chain calls yet. Data (`BASKET`, `EXAMPLE_PRICES`)
mirrors `config/sandick.basket.json` and `config/prices.example.json`.

### Going live

`chain.js` is the start of that wiring: a `viem` layer exposing the vault's
read/write surface (`totalAssets`, `sharePrice`, `balanceOf`, `convertToAssets`,
`pendingRedeemShares`, `claimableAssets`, `deposit`, `requestRedeem`, `redeem`,
`claim`). Enable it by filling in `config.js` and setting `chain.enabled = true`:

```js
// config.js
window.SANDICK_CONFIG = { chain: {
  enabled: true,
  chainId: 998,
  rpcUrl: 'https://rpc.hyperliquid-testnet.xyz/evm',
  vaultAddress: '0x…',   // deployed SandickVault
  usdcAddress: '0x…',    // vault underlying
}};
```

```js
import { SandickChain } from './chain.js';
const chain = await SandickChain.connect(window.SANDICK_CONFIG.chain);
const nav = await chain.totalAssets();
await chain.approveUsdc(amount);
await chain.deposit(amount);
```

It's **off by default** — testnet sign-off (chainid 998) isn't complete, so the
contract immutables it reads against must be verified first (see root `TODO.md`).
Because CoreWriter actions settle asynchronously and can fail silently, callers
must confirm by **re-reading state**, never by trusting a tx receipt.
