# 🏳️‍🌈 Scott is gay 🏳️‍🌈

# Sandick — HIP-3 equal-weighted basket vault

Tooling to build an **equal-weighted** position across the **SANDICK** basket on
Hyperliquid [HIP-3](https://hyperliquid.gitbook.io/hyperliquid-docs) (builder-deployed)
perp markets — the seven AI / data-center / storage names whose logos spell **S A N D I C K**:

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
  cli.py         # dry-run CLI + table rendering
config/
  sandick.basket.json
  prices.example.json
tests/           # pytest suite (run: python -m pytest)
```

## Roadmap

- [ ] **Live order placement** via `Exchange` (API wallet) behind an explicit
      `--execute` confirmation, with slippage caps and reduce-only rebalancing.
- [ ] **Rebalance** mode: read current positions and trade only the deltas back
      to equal weight.
- [ ] **HIP-3 market deployment** helper (stake HYPE, register the perp dex).

## Tests

```bash
python -m pytest
```
