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
| `app.js`          | UI logic; demo state machine by default, live chain calls when enabled. |
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

By default (`chain.enabled = false`) the vault stats, balances, queue and admin
actions run on a **local demo state machine** — no chain calls. Data (`BASKET`,
`EXAMPLE_PRICES`) mirrors `config/sandick.basket.json` and
`config/prices.example.json`.

### Going live

`app.js` is wired to the chain through `chain.js` (a `viem` layer over the
deployed `SandickVault`). Flip `chain.enabled = true` in `config.js` and the
same buttons hit the contract instead of the demo state:

```js
// config.js
window.SANDICK_CONFIG = { chain: {
  enabled: true,
  chainId: 998,
  rpcUrl: 'https://rpc.hyperliquid-testnet.xyz/evm',
  vaultAddress: '0x…',   // deployed SandickVault
  usdcAddress: '0x…',    // vault underlying (USDC)
}};
```

What's live when enabled:

- **Connect** — real wallet (`window.ethereum`); the button shows the address.
- **Depositor** — NAV/share + supply + your shares/USDC read from the vault;
  **Deposit** runs `approve` (if needed) then `deposit`; **Redeem** runs the
  sync `redeem`; **Request redeem** runs `requestRedeem`; the queue renders the
  contract's `pendingRedeemShares` / `claimableAssets`; **Claim** runs `claim`.
- **Admin** — the panel unlocks only if the connected wallet is the vault
  `manager` or `owner`. **Bridge** prompts a direction/amount and runs
  `bridgeToCore` / `bridgeFromCore`. (Pause/allow-list helpers exist on
  `chain.js`.)

Still off-chain: **submit basket** and **rebalance** order *encoding* (HIP-3
asset ids + 1e8-scaled px/sz) is produced by the Python planner
(`sandick.onchain` / `exec_cli`); `chain.submitBasket(orders)` is the ready hook
to send that output.

It's **off by default** — testnet sign-off (chainid 998) isn't complete, so the
contract immutables it reads against must be verified first (see root `TODO.md`).
Because CoreWriter actions settle asynchronously and can fail silently, the live
handlers confirm by **re-reading state** after each tx, never by trusting a
receipt.
