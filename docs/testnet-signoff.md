# SANDICK — testnet sign-off runbook (chainid 998)

> **Status: unaudited, testnet only.** This runbook proves the live round-trip on
> HyperEVM testnet before any mainnet deposits. It is the gate every other
> "go-live" task waits on. Do **not** run the `--execute` / mainnet variants until
> every step here passes and a security audit is complete.

The architecture is simulation- and unit-tested only (220 Python + 51 contract
tests, plus an invariant/fuzz harness). What's *unproven* is the live chain behaviour — the precompile reads, the
USDC↔Core bridging, and the one load-bearing assumption: that the **vault
contract account can place HIP-3 orders via CoreWriter** on the Trade.xyz dex.

Work top to bottom. Each step says what to run and what "pass" looks like.

---

## 0. Prerequisites

- A funded HyperEVM **testnet** key (gas) and the **manager** key (separate from
  the owner key ideally).
- Testnet USDC for the vault's underlying.
- Tooling: `pip install -e ".[keeper,live]"` and `npm ci`.
- Network egress to `api.hyperliquid.xyz` / the testnet RPC (this sandbox blocks
  it — run from an allowlisted host).

Set the shared env once:

```bash
export RPC_URL=https://rpc.hyperliquid-testnet.xyz/evm
export PRIVATE_KEY=0x…        # deployer (gas)
export VAULT_OWNER=0x…        # governance key
export VAULT_MANAGER=0x…      # strategy key (trades only)
export USDC_ADDRESS=0x…       # testnet USDC the vault will custody
```

---

## 1. Calibrate the on-chain immutables from live data

```bash
python -m sandick.deploy_config --dex-name <real-trade.xyz-dex> --out config/deploy.json
```

**Pass:** `config/deploy.json` contains a plausible `perpDexIndex`, an `assetId`
for **every** basket coin, USDC's `usdcSystemAddress` + `usdcCoreTokenIndex`, and
a `coreScale`. Sanity-check each against the docs/explorer — these are the
"unverified inputs" the contract comments warn about.

> ⚠️ Replace the placeholder basket symbols (`KIOXIA`, dex `tradexyz`) with the
> **real** Trade.xyz dex name + coin symbols first, in
> `config/sandick.basket.json`. Confirm the vault's USDC is 6-decimal (else the
> derived `coreScale` is wrong).

## 2. Deploy the reader + vault, allow-list the basket

```bash
node scripts/deploy.js config/deploy.json --execute
```

**Pass:** the `HyperCoreReader` and `BasketVault` are deployed and each basket
`assetId` is allow-listed (`allowedAsset[id] == true`). Record the vault address:

```bash
export VAULT_ADDRESS=0x…      # the deployed BasketVault
```

## 3. Seed the vault's HyperCore account

Before opening deposits, initialise the vault's Core account (a tiny
deposit/bridge), because the `accountMarginSummary` precompile's behaviour for a
**never-initialised** account is unconfirmed.

**Pass:** decide and document the fresh-account behaviour — does the precompile
**revert** or **return zeros**? `HyperCoreReader.accountEquityUsd` and
`Web3KeeperClient.core_available` both assume a clean read; `core_available`
treats a revert as `0` (safe), but confirm `totalAssets()` doesn't revert on a
zero-equity account.

## 4. Verify the calibrated immutables on-chain

Read them back from the deployed contracts and confirm they match `deploy.json`:

- `vault.reader()` → reader address; `reader.perpDexIndex()`,
  `reader.marginSummaryPrecompile()`.
- `vault.coreScale()`, `vault.usdcSystemAddress()`, `vault.usdcCoreTokenIndex()`,
  `vault.tif()`.

**Pass:** every value equals the calibrated input.

## 5. Prove NAV reads (the share-pricing path)

```bash
# Off-chain read-only check that the vault sees the dex + collateral:
python -m sandick.exec_cli verify --use-env

# Keeper preview — exercises totalAssets / idle / core_available end to end:
python -m sandick.keeper_cli --once         # PREVIEW, nothing sent
```

**Pass:** `verify` succeeds; the keeper prints a tick with sensible
`liquidity: ok` / `would bridge …` and no read errors. `totalAssets()` reflects
the seeded equity.

## 6. Prove order placement — THE load-bearing assumption 🔴

Submit a **single small** allow-listed leg from the manager key and confirm it
fills. Either path works (same on-chain `submitBasket`):

```bash
# Smallest possible: build one-leg orders with the planner, submit via the adapter
python -m sandick.exec_cli run --capital 200 --prices config/prices.example.json
# …then submit the encoded orders through Web3KeeperClient.submit_basket(orders)
```

**Pass:** the order appears on the Trade.xyz dex and fills; the vault's position
shows up in `positions()` and Core equity. **If this fails, stop** — a contract
account can't trade the dex as assumed, and the on-chain design needs revisiting
before anything else matters.

## 7. End-to-end round trip

Walk the full lifecycle and assert NAV/shares stay consistent at each hop:

1. **deposit** USDC → receive SAND-LP shares (front end with `chain.enabled`, or
   `cast`/script).
2. **bridgeToCore** → USDC moves to the perp margin account.
3. **submitBasket** → basket positions open; `totalAssets()` reflects equity.
4. **rebalance** → keeper (`--execute`) trades only the deltas after drift.
5. **requestRedeem** → shares escrow; **bridgeFromCore** → idle USDC returns.
6. **fulfillRedeem** (permissionless) → **claim** → depositor gets USDC back.

**Pass:** share price is continuous across the multi-block bridges (no NAV jump
when USDC is parked mid-bridge — see step 8), and the redeemer's proceeds match
the share price at fulfilment.

## 8. Bridging + read-timing edge cases

- Confirm USDC parked in the Core **spot** account mid-bridge isn't dropped from
  NAV. Today `_coreSpotUsd()` returns 0; if the gap is material, wire the
  spot-balance precompile (TODO §4) and re-run step 7.
- Confirm the start-of-block read timing of `accountMarginSummary` against
  deposits/rebalances in the same block (stale-read handling, TODO §4).
- Verify `HyperliquidMarketData.positions()` parses the dex's `assetPositions`
  payload (the one untested line in `keeper_chain.py`).

---

## Sign-off checklist

- [ ] §1 immutables calibrated and eyeballed
- [ ] §2 reader + vault deployed, assets allow-listed
- [ ] §3 Core account seeded; fresh-account read behaviour documented
- [ ] §4 on-chain immutables match `deploy.json`
- [ ] §5 NAV reads succeed (verify + keeper preview)
- [ ] §6 **single order places and fills from the vault** 🔴
- [ ] §7 full deposit→trade→rebalance→redeem→claim round trip
- [ ] §8 bridge/spot-NAV/stale-read/positions edge cases confirmed

When all eight pass, the open architectural assumptions are closed. **Mainnet
still requires a full security audit** (custody + share accounting + CoreWriter
integration + redemption queue) before real deposits.
