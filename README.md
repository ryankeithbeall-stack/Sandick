# Aperture — HIP-3 basket vault platform

**Aperture** is a **vault deployment platform** on Hyperliquid
[HIP-3](https://hyperliquid.gitbook.io/hyperliquid-docs) (builder-deployed) perp
markets. Anyone can deploy an **admin-managed, equal-weighted basket vault**
through the on-chain [`VaultFactory`](contracts/src/VaultFactory.sol); the platform
takes a **protocol fee** on every vault it hosts (see
[On-chain vault](#on-chain-vault-custom-trustless)). The **SANDICK** basket below
is the **flagship vault** — the first one deployed through the factory and the
reference for the tooling in this repo.

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

| Company     | Ticker | Coin (perp) |
|-------------|--------|-------------|
| SanDisk     | SNDK   | `SNDK`      |
| Arm Holdings| ARM    | `ARM`       |
| Nebius      | NBIS   | `NBIS`      |
| Dell        | DELL   | `DELL`      |
| Intel       | INTC   | `INTC`      |
| CoreWeave   | CRWV   | `CRWV`      |
| Kioxia      | 285A   | `KIOXIA`    |

Each asset receives an equal share (**1 / 7 ≈ 14.29 %**) of the gross notional.

## v1 scope: calculator + dry-run

This version **computes the orders but never sends them.** It takes a capital
amount, a leverage multiple and a set of mark prices, and prints exactly what an
equal-weighted entry would look like. Live order placement is intentionally left
for a later iteration (see [Roadmap](#roadmap)).

## Install

The base package has **no runtime dependencies**; each capability pulls its deps
from an optional-dependency group (an "extra"). Install only what you need:

| Command                          | Enables                                                        |
|----------------------------------|---------------------------------------------------------------|
| `pip install -e .`               | Dry-run planner from a local price file (`sandick` CLI).       |
| `pip install -e ".[live]"`       | `--live` price fetching + off-chain order placement.           |
| `pip install -e ".[keeper,live]"`| The keeper bot (EVM reads/writes + live market data).          |
| `pip install -e ".[dev]"`        | The test + lint toolchain (`pytest`, `pytest-cov`, `ruff`).    |
| `pip install -r requirements.txt`| Everything above in one shot (mirrors all extras).             |

> Live/keeper deps are imported lazily, so a missing extra surfaces only when you
> actually run that path (e.g. `--execute`), not at import time. If a command
> errors on a missing module, install the matching extra from the table above.

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
  285A    KIOXIA  LONG       150.00       66.66      $9,999.00  14.29%
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
    --select SNDK,ARM,NBIS,DELL,INTC,CRWV,KIOXIA \
    --dex sandick --name SANDICK \
    --catalog catalog.json --out config/sandick.basket.json

# 3. Dry-run the resulting basket (see Usage above).
python -m sandick.cli --capital 70000 --basket config/sandick.basket.json --live
```

`build-basket` resolves each selected coin's `sz_decimals` from the catalog,
disambiguates coins that appear on multiple dexes (qualify them as `dex:COIN`),
and writes a ready-to-use basket. The grouping/weighting is equal-weight today;
custom groupings are on the roadmap.

## Execution (native vault, testnet-first)

Once a basket is built, the execution CLI plans and (optionally) places the
orders on a **native Hyperliquid vault** that trades the **Trade.xyz** HIP-3 dex.
It is conservative by design — it handles pooled depositor funds:

- **testnet by default** (mainnet needs `--mainnet`)
- **preview unless `--execute`** is passed
- **marketable limit orders with a `--slippage` cap** (never naked market orders)
- a **`--max-notional` circuit breaker**
- credentials from the environment only

```bash
# Read-only: prove the vault can see the dex + collateral (testnet).
python -m sandick.exec_cli verify --use-env

# Preview the orders (no creds, no send):
python -m sandick.exec_cli run --capital 70000 --prices config/prices.example.json

# Send on testnet, capped:
HL_SECRET_KEY=0x... HL_VAULT_ADDRESS=0x... \
  python -m sandick.exec_cli run --capital 70000 --live --execute --max-notional 80000
```

Environment variables (only needed for `verify --use-env` / `run --execute`):

| Var                  | Purpose                                              |
|----------------------|------------------------------------------------------|
| `HL_SECRET_KEY`      | API/agent wallet private key (signs for the vault).  |
| `HL_VAULT_ADDRESS`   | The native vault address to trade on behalf of.      |
| `HL_ACCOUNT_ADDRESS` | (optional) master account address.                   |

> **Before mainnet,** run `verify` and a small testnet `run --execute` to confirm
> a native vault can place orders on the Trade.xyz HIP-3 dex (`dex:COIN` coins via
> `Exchange(vault_address=...)`). This is the one assumption the design rests on.

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
  weights.py     # equal / explicit / grouped target-weight resolution
  discovery.py   # enumerate HIP-3 assets across perp dexes
  admin.py       # admin CLI: discover assets + build a basket
  plan.py        # serialize a plan to a reviewable JSON artifact
  execute.py     # order intents, slippage/tick rounding, safe submission
  exec_cli.py    # execution CLI: verify + run
  rebalance.py   # delta orders back to target weight
  onchain.py     # plan -> on-chain submitBasket orders (HIP-3 asset ids, 1e8)
  deploy_config.py # derive on-chain immutables from live data
  keeper.py      # pure keeper decision logic (liquidity buffer + drift signal)
  keeper_bot.py  # keeper orchestration: read -> act -> verify (KeeperClient seam)
  keeper_chain.py # live web3 adapter: vault reads + margin precompile + writes
  keeper_cli.py  # operator CLI: assemble config + run the keeper (preview/execute)
  cli.py         # dry-run CLI + table rendering
config/
  sandick.basket.json
  prices.example.json
tests/           # pytest suite (run: python -m pytest)
```

## On-chain vault (custom, trustless)

A fully on-chain, tokenized vault platform lives in [`contracts/`](contracts/):

- **`VaultFactory`** — the platform. Anyone calls `createVault(...)` to deploy
  their own vault; the factory registers it (enumerable via `allVaults()`) and
  stamps it with the **platform fee** + treasury. The factory stays each vault's
  `protocolAdmin`, so the platform can adjust the fee it earns but a vault
  operator can never zero it out.
- **`BasketVault`** — an ERC-4626 HyperEVM vault that custodies USDC and trades
  the HIP-3 basket on HyperCore via CoreWriter. Depositors get transferable
  shares; the manager can trade but never withdraw funds. Each vault charges its
  operator's mgmt/perf/exit fees **plus** the platform fee (all as dilution
  shares; no USDC ever leaves the vault).

The off-chain planner feeds a vault via `sandick.onchain.plan_to_onchain_orders`
(HIP-3 asset ids + 1e8-scaled prices). See
[contracts/README.md](contracts/README.md). **Unaudited — testnet only.**

## Architecture decisions

- **Vault:** native Hyperliquid vault (~100 USDC to create; leader keeps ≥5%).
  Depositors are protocol-enforced deposit-only and PnL is split for us — no
  custom accounting. The admin runs the basket via `Exchange(vault_address=...)`.
- **Dex scope:** a single Trade.xyz HIP-3 dex, so all legs share one USDC
  collateral pool (no cross-dex margin fragmentation).

## Deploy the platform + flagship vault (testnet)

```bash
# 1. Derive the on-chain immutables from live data (perpDexs/meta/spotMeta):
python -m sandick.deploy_config --dex-name tradexyz --out config/deploy.json

# 2. Deploy reader + VaultFactory, create the flagship vault through the factory,
#    and allow-list the basket assets (dry-run by default):
RPC_URL=... PRIVATE_KEY=... VAULT_OWNER=0x... VAULT_MANAGER=0x... USDC_ADDRESS=0x... \
  PROTOCOL_TREASURY=0x... PROTOCOL_FEE_BPS=100 \
  node scripts/deploy.js config/deploy.json --execute
```

Step 2 deploys the `HyperCoreReader` + `VaultFactory` (owned by `PLATFORM_OWNER`,
default `VAULT_OWNER`), then calls `factory.createVault(...)` to mint the flagship
`BasketVault`, allow-lists the basket assets, and hands vault ownership to
`VAULT_OWNER`. `PROTOCOL_FEE_BPS` (≤ 200 = 2%/yr, default 100) is the platform fee
streamed to `PROTOCOL_TREASURY` from every vault. `deploy_config` computes the
Trade.xyz `perpDexIndex`, each coin's HIP-3 asset id, USDC's system address /
token index, and the EVM↔Core `coreScale`.

## Keeper bot (testnet-first)

The keeper keeps the live vault healthy between manual interventions: it bridges
USDC back from Core to cover queued redemptions + a buffer, and submits delta
orders when basket weights drift. Decision logic is pure (`keeper.py`);
orchestration (`keeper_bot.py`) reads → acts → **verifies by re-reading**
(CoreWriter can fail silently); the live wiring is `keeper_chain.py`.

```python
from sandick.keeper_bot import KeeperBot, run_loop
from sandick.keeper_chain import Web3KeeperClient, HyperliquidMarketData

client = Web3KeeperClient.from_endpoint(
    rpc_url="https://rpc.hyperliquid-testnet.xyz/evm",
    vault_address="0x…", usdc_address="0x…",
    private_key=os.environ["MANAGER_KEY"],          # the vault manager key
    market_data=HyperliquidMarketData("0x…vault", coins=[...], dex="tradexyz"),
)
bot = KeeperBot(
    client=client, target_weights={...}, sz_decimals={...}, asset_ids={...},
    dry_run=True,                                    # flip to False to transmit
)
run_loop(bot, interval=60)                           # tick() forever
```

`dry_run=True` (the default) plans and logs but never sends — flip it only after
testnet sign-off. Install the adapter deps with `pip install -e ".[keeper,live]"`.

Or run it straight from the CLI — it assembles weights/sizes from the basket and
`assetId`s from `config/deploy.json`, and is **preview-only until `--execute`**:

```bash
# Preview one tick against testnet (no key, nothing sent):
RPC_URL=… VAULT_ADDRESS=0x… USDC_ADDRESS=0x… \
  python -m sandick.keeper_cli --once

# Run live, transmitting (manager key required):
RPC_URL=… VAULT_ADDRESS=0x… USDC_ADDRESS=0x… MANAGER_KEY=0x… \
  python -m sandick.keeper_cli --execute --interval 60
```

## Roadmap

`[x]` below means **the code is written and unit-tested**, not that it has been
verified end-to-end against a live chain. Everything marked ⚠️ depends on the one
unproven assumption — *a contract account can place HIP-3 orders via CoreWriter on
the real dex* — which is only validated by **Testnet sign-off** (still open). Do
not treat the ⚠️ items as production-proven until that box is checked; see
[GO-LIVE.md](GO-LIVE.md).

**Implemented (unit-tested, offline):**

- [x] **Custom groupings/weights** beyond a single equal-weighted set.
- [x] **Live order placement** (off-chain) behind `--execute`, slippage + notional guards. ⚠️
- [x] **On-chain vault** (ERC-4626) with NAV reader and async redemption queue. ⚠️
- [x] **Rebalance** mode: trade only the deltas back to target weight (reduce-only aware). ⚠️
- [x] **Deploy + calibration** scripts to derive on-chain immutables from live data. ⚠️

**Not yet verified / open:**

- [ ] **Testnet sign-off:** deploy to chainid 998, seed the Core account, and confirm
      orders/NAV/bridging end-to-end (this is what clears the ⚠️ items above).
- [ ] **Security audit** before any mainnet deposits.

## Tests

```bash
# Python suite (runs fully offline — the hyperliquid SDK is faked in tests):
python -m pytest

# With coverage (enforces a 90% floor — see pyproject.toml):
pip install -e ".[dev]"
python -m pytest --cov=sandick --cov-report=term-missing

# Solidity/EVM contract tests (in-process on @ethereumjs/vm):
npm ci && npm run test:contracts

# Solidity line coverage (instruction-derived; writes coverage/contracts/):
npm run coverage:contracts
```

Both suites run in CI on every push and pull request
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) — Python (ruff + pytest,
90% coverage gate), contracts (tests + 85%/80% total/per-file coverage gate), and
an informational [Slither](docs/security/README.md) static-analysis job.

## Governance, operations & security docs

Read before taking real funds — the human gate is [`docs/LAUNCH_SIGNOFF.md`](docs/LAUNCH_SIGNOFF.md).

- **Risk & threats:** [`docs/RISK_REGISTER.md`](docs/RISK_REGISTER.md) · [`docs/security/THREAT_MODEL.md`](docs/security/THREAT_MODEL.md) · [`docs/security/SECURITY_TEST_PLAN.md`](docs/security/SECURITY_TEST_PLAN.md) · [`docs/security/README.md`](docs/security/README.md)
- **Operations:** [`docs/INCIDENT_AND_SHUTDOWN.md`](docs/INCIDENT_AND_SHUTDOWN.md) · [`docs/ops/MONITORING_POLICY.md`](docs/ops/MONITORING_POLICY.md) · [`docs/ops/RECOVERY_DRILL_RUNBOOK.md`](docs/ops/RECOVERY_DRILL_RUNBOOK.md) · [`docs/ops/ROLE_AND_CUSTODY_POLICY.md`](docs/ops/ROLE_AND_CUSTODY_POLICY.md) · [`docs/ops/KEY_ROTATION_POLICY.md`](docs/ops/KEY_ROTATION_POLICY.md)
- **Protocol:** [`docs/protocol/PRECOMPILE_SAFETY.md`](docs/protocol/PRECOMPILE_SAFETY.md) · [`docs/protocol/COREWRITER_ACTION_MATRIX.md`](docs/protocol/COREWRITER_ACTION_MATRIX.md) · [`docs/protocol/MARKET_SUITABILITY.md`](docs/protocol/MARKET_SUITABILITY.md)
- **Depositor-facing:** [`docs/depositor-guide.md`](docs/depositor-guide.md) · [`docs/risk-disclosures.md`](docs/risk-disclosures.md) · [`docs/testnet-signoff.md`](docs/testnet-signoff.md)
