# 🏳️‍🌈 Scott is gay 🏳️‍🌈

# Sandick — HIP-3 equal-weighted basket vault

Tooling for an **admin-managed, equal-weighted basket vault** on Hyperliquid
[HIP-3](https://hyperliquid.gitbook.io/hyperliquid-docs) (builder-deployed) perp
markets.

## Roles

- **Admin (you).** Selects which HIP-3 assets make up the basket (from any perp
  dex), equal-weighted. Manages the strategy. The asset list is fully
  configurable — see [Admin workflow](#admin-workflow).
- **Depositors (everyone else).** Cannot pick assets or trade. They only
  **deposit USDC** into the vault and withdraw later; their funds ride the
  admin's basket and share PnL pro-rata. *(Deposit/withdraw wiring is pending a
  design decision — see [Roadmap](#roadmap).)*

The **SANDICK** basket below is just the shipped example: the seven AI /
data-center / storage names whose logos spell **S A N D I C K**.

| Company    | Ticker | Coin (perp) |
|------------|--------|-------------|
| SanDisk    | SNDK   | `SNDK`      |
| Astera Labs| ALAB   | `ALAB`      |
| Nebius     | NBIS   | `NBIS`      |
| Dell       | DELL   | `DELL`      |
| Intel      | INTC   | `INTC`      |
| CoreWeave  | CRWV   | `CRWV`      |
| Kioxia     | 285A   | `KIOXIA`    |

Each asset receives an equal share (**1 / 7 ≈ 14.29 %**) of the gross notional.

## v1 scope: calculator + dry-run

This version **computes the orders but never sends them.** It takes a capital
amount, a leverage multiple and a set of mark prices, and prints exactly what an
equal-weighted entry would look like. Live order placement is intentionally left
for a later iteration (see [Roadmap](#roadmap)).

## Install

```bash
pip install -r requirements.txt   # SDK only needed for --live
# or, editable install with the `sandick` CLI entry point:
pip install -e .
```

## Usage

Dry-run from a local price file (no network required):

```bash
python -m sandick.cli --capital 70000 --prices config/prices.example.json
```

```
==============================================================================
  SANDICK HIP-3 VAULT — EQUAL-WEIGHTED PLAN  (DRY RUN — no orders sent)
==============================================================================
  Basket: SANDICK    Dex: sandick    Assets: 7
  Capital: $70,000.00    Leverage: 1x    Side: LONG    Gross notional: $69,997.39
------------------------------------------------------------------------------
  TICKER  COIN    SIDE        PRICE        SIZE       NOTIONAL  WEIGHT
------------------------------------------------------------------------------
  SNDK    SNDK    LONG        50.00      200.00     $10,000.00  14.29%
  ...
  285A    KIOXIA  LONG        13.00      769.23      $9,999.99  14.29%
------------------------------------------------------------------------------
  Deployed margin: $69,997.39    Residual cash (rounding): $2.61
==============================================================================
```

Options:

| Flag          | Default | Description                                            |
|---------------|---------|--------------------------------------------------------|
| `--capital`   | (req'd) | Margin capital in USDC to deploy.                      |
| `--leverage`  | `1.0`   | Gross-notional / capital multiple.                     |
| `--side`      | `long`  | `long` or `short` for every leg.                       |
| `--prices`    | —       | Path to a `{coin: price}` JSON file.                   |
| `--live`      | —       | Pull live mids from Hyperliquid instead of a file.     |
| `--testnet`   | —       | With `--live`, use testnet.                            |
| `--basket`    | `config/sandick.basket.json` | Basket definition to use.         |

### Live prices

`--live` uses the official `hyperliquid-python-sdk` to fetch mids from the HIP-3
perp dex named in the basket config. It only works where `api.hyperliquid.xyz`
is reachable — in sandboxes with an egress allowlist you'll get a clean
`Host not in allowlist` error and a non-zero exit.

```bash
python -m sandick.cli --capital 70000 --live
```

## Admin workflow

The admin assembles the basket by **selecting** from the assets that actually
exist across Hyperliquid's perp dexes — nothing is hard-coded.

```bash
# 1. Discover every available HIP-3 asset and snapshot the catalog.
#    (needs an allowlisted host; in this sandbox the Hyperliquid host is blocked)
python -m sandick.admin discover --out catalog.json

# 2. Pick the assets you want -> writes an equal-weighted basket config.
python -m sandick.admin build-basket \
    --select SNDK,ALAB,NBIS,DELL,INTC,CRWV,KIOXIA \
    --dex sandick --name SANDICK \
    --catalog catalog.json --out config/sandick.basket.json

# 3. Dry-run the resulting basket (see Usage above).
python -m sandick.cli --capital 70000 --basket config/sandick.basket.json --live
```

`build-basket` resolves each selected coin's `sz_decimals` from the catalog,
disambiguates coins that appear on multiple dexes (qualify them as `dex:COIN`),
and writes a ready-to-use basket. The grouping/weighting is equal-weight today;
custom groupings are on the roadmap.

## Configuration

- **`config/sandick.basket.json`** — the basket: dex name, coin symbols and each
  asset's `sz_decimals` (size rounding precision). Edit `dex`/`coin` to match the
  perp dex you actually deploy on Hyperliquid.
- **`config/prices.example.json`** — illustrative mark prices for dry-runs. Keys
  must match the `coin` values in the basket. Keys starting with `_` are ignored.

## How the math works

Given `capital` (margin, USDC) and `leverage`:

```
gross_notional      = capital * leverage
per_asset_notional  = gross_notional / N          # N = 7, equal weight
size_i              = floor( per_asset_notional / price_i , sz_decimals_i )
notional_i          = size_i * price_i
margin_i            = notional_i / leverage
```

Sizes are **floored** to each asset's precision so the plan never over-deploys;
the small leftover shows up as `Residual cash`.

## Project layout

```
sandick/
  basket.py      # Basket / BasketAsset models + JSON loading
  allocator.py   # pure equal-weight sizing math (no network)
  prices.py      # price sources: local file or live Hyperliquid mids
  discovery.py   # enumerate HIP-3 assets across perp dexes
  admin.py       # admin CLI: discover assets + build a basket
  cli.py         # dry-run CLI + table rendering
config/
  sandick.basket.json
  prices.example.json
tests/           # pytest suite (run: python -m pytest)
```

## Roadmap

- [ ] **Vault deposits/withdrawals (depositor role).** Decide between a
      Hyperliquid **native vault** (built-in depositor accounting + PnL split,
      recommended) vs. a custom vault contract/ledger. This unlocks the
      depositor role. Open question to verify against live docs: whether a
      native vault can hold positions on HIP-3 builder dexes, and whether HIP-3
      collateral is unified or isolated per dex.
- [ ] **Live order placement** via `Exchange` (API wallet) behind an explicit
      `--execute` confirmation, with slippage caps and reduce-only rebalancing.
- [ ] **Rebalance** mode: read current positions and trade only the deltas back
      to equal weight.
- [ ] **Custom groupings/weights** beyond a single equal-weighted set.
- [ ] **HIP-3 market deployment** helper (stake HYPE, register the perp dex) —
      only needed if the admin deploys their own markets.

## Tests

```bash
python -m pytest
```
