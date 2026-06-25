# Aperture — HIP-3 Market Suitability Procedure

> **Status: UNAUDITED, testnet-only.** This is the repeatable procedure to vet
> each HIP-3 basket asset **before** allow-listing it on a `BasketVault`. It
> exists because the **#1 load-bearing risk is unverified market metadata**: the
> repo ships placeholders — dex `tradexyz`, coin symbols (e.g. `KIOXIA`), and an
> unverified `perpDexIndex` / `assetIds` / `coreScale`. See Risk Register
> [**R-01**](../RISK_REGISTER.md) (BLOCKER) and **R-10** (under-configured vault),
> and [`GO-LIVE.md`](../../GO-LIVE.md) §5 "Real Trade.xyz data".

An admin assembles a basket by **selecting** from assets that already exist
across Hyperliquid's perp dexes (the core dex plus every HIP-3 builder dex). The
contract trusts whatever asset IDs it is allow-listed with — there is no
on-chain validation that an asset id is live, two-sided, or even real. This
procedure is the off-chain gate. **Do not hardcode guesses.**

## Tooling this procedure drives

| Step | Code | What it does |
|---|---|---|
| Enumerate the catalog | `sandick/discovery.py` (`discover_assets`, `parse_perp_dexs`, `parse_meta_universe`) | lists every dex + asset; **drops `isDelisted`** automatically |
| Build the basket | `sandick/admin.py` (`build-basket` → `resolve_selection`) | maps chosen symbols to `AssetInfo`, erroring on miss/ambiguity |
| Calibrate immutables | `sandick/deploy_config.py` (`build_deploy_config`, `find_perp_dex_index`, `asset_ids_for`, `core_scale`, `usdc_system_address`) | resolves `perpDexIndex`, asset IDs, USDC token index + system address, `coreScale` |
| Asset-id formula | `sandick/onchain.py` (`hip3_asset_id`) | `100000 + perpDexIndex*10000 + indexInMeta` |

All four run from a host with egress to `api.hyperliquid.xyz` (blocked in the dev
sandbox — see [`GO-LIVE.md`](../../GO-LIVE.md) §5 note).

## The vetting checklist (per candidate asset)

Run these **live** `info` queries (Hyperliquid SDK / `POST /info`) for each
candidate before it goes into `--select`:

1. **Dex exists and is the intended one.** `{"type":"perpDexs"}`. Resolve the
   builder dex by **name or deployer**, not position — `find_perp_dex_index`
   skips the `null` element-0 (the core dex). Record the index `i`; it feeds the
   asset-id formula. **Reject** if the placeholder `tradexyz` does not resolve to
   a real listed dex.
2. **Asset is listed and not delisted.** `{"type":"meta","dex":<name>}` →
   `universe`. `parse_meta_universe` already skips any entry flagged
   `isDelisted`. **Reject** any symbol absent from the live `universe` (this is
   exactly the placeholder risk — confirm each of the seven SANDICK names is
   really listed, substituting any that aren't while keeping the spelling).
3. **Collateral is USDC (the canonical spot-collateral token).**
   `{"type":"spotMeta"}` → find the `USDC` token by name; confirm its `index`
   (resolved dynamically, not hardcoded). `build_deploy_config` reads
   `usdcCoreTokenIndex` + `weiDecimals` from here and derives
   `usdcSystemAddress` via `usdc_system_address(index)`. **Reject** a dex whose
   margin collateral is not USDC — the vault's bridge path (`usdClassTransfer`
   action 7 + `spotSend` action 6 in `HyperCoreActions.sol`) assumes USDC.
4. **Two-sided book with real depth.** `{"type":"l2Book","coin":<coin>}`.
   Require a non-empty **bid *and* ask** with enough size to absorb the basket's
   per-asset notional at acceptable slippage. A one-sided or empty book means
   `submitBasket` IOC/ALO legs won't fill (CoreWriter is fire-and-forget and
   **never reverts** on a no-fill — see R-08). **Reject** thin/one-sided books.
5. **Liquidity / open-interest headroom.** `{"type":"metaAndAssetCtxs"}` for
   per-asset `openInterest`, `dayNtlVlm`, and mark/oracle prices; cross-check
   `{"type":"perpsAtOpenInterestCap"}`. **Reject** an asset already at its OI cap
   (you can't open into it) or with negligible volume.
6. **Resolve `sz_decimals` + the asset id.** Take `szDecimals` from the
   `universe` entry (stored on `AssetInfo.sz_decimals`, carried into the basket
   JSON). Compute the on-chain id with `hip3_asset_id(perpDexIndex, indexInMeta)`
   = `100000 + perpDexIndex*10000 + indexInMeta`, where `indexInMeta` is the
   asset's position in that dex's `universe`. `asset_ids_for` does this for the
   whole selection. Wrong `sz_decimals` mis-sizes every order; a wrong id trades
   the wrong (or a non-existent) market.

A candidate must pass **all six** to enter `--select`.

## Worked sequence

```bash
# 1. Snapshot the live catalog (delisted auto-dropped):
python -m sandick.admin discover --testnet --out catalog.json

# 2. Build the basket from vetted symbols (offline, from the snapshot):
python -m sandick.admin build-basket \
    --select SNDK,ARM,NBIS,DELL,INTC,CRWV,KIOXIA \
    --dex tradexyz --name SANDICK \
    --catalog catalog.json --out config/sandick.basket.json

# 3. Calibrate on-chain immutables from LIVE data (needs egress):
python -m sandick.deploy_config --dex-name tradexyz --out config/deploy.json
#   -> perpDexIndex, assetIds (hip3_asset_id), usdcCoreTokenIndex,
#      usdcSystemAddress, coreScale, tif=3 (IOC)
```

`build-basket` fails closed on a bad selection: `resolve_selection` raises
`KeyError` for an unknown symbol and `ValueError` for one ambiguous across dexes
(qualify it as `dex:COIN`). `deploy_config` raises `KeyError` if the dex or a
coin isn't in the live `universe`, and `core_scale` raises if
`core_wei_decimals < evm_decimals` (sub-unit scaling needs explicit handling —
confirm the vault's EVM USDC is 6-decimal per [`GO-LIVE.md`](../../GO-LIVE.md) §5).

## After calibration

- **Verify on-chain.** Reconcile `config/deploy.json` against the deployed
  reader/vault immutables — [`docs/testnet-signoff.md`](../testnet-signoff.md)
  §1/§4 ("Calibrate" / "Verify the calibrated immutables").
- **Allow-list explicitly.** The constructor does **not** allow-list anything.
  The operator MUST call `setAllowedAsset` for each vetted id (and set
  `setOrderCaps` / `setAssetOrderCap`) post-deploy — this is Risk **R-10**
  (under-configured vault) and [`GO-LIVE.md`](../../GO-LIVE.md) §1.
- **Prove placement.** None of the above proves a *contract* account can trade
  the dex — that is the unproven assumption R-01, gated by
  [`GO-LIVE.md`](../../GO-LIVE.md) §2 / [`docs/testnet-signoff.md`](../testnet-signoff.md)
  §6. A passing suitability check is a precondition, not a substitute.

## Re-vetting cadence

Markets change. Re-run steps 2 (delisting), 4–5 (depth / OI cap) before any
basket rebalance that adds an asset, and on a schedule for live baskets — a
once-listed asset can be delisted or hit its OI cap later, which `submitBasket`
will silently fail to trade.

## Cross-references

- [`docs/RISK_REGISTER.md`](../RISK_REGISTER.md) — R-01 (placeholders), R-10 (config), R-08 (silent no-fill), R-11 (legal)
- [`GO-LIVE.md`](../../GO-LIVE.md) §1/§2/§5 — deploy, prove placement, real data
- [`docs/testnet-signoff.md`](../testnet-signoff.md) §1/§4/§6 — calibrate, verify, prove
- [`docs/security/THREAT_MODEL.md`](../security/THREAT_MODEL.md) — permissionless-factory / brand risk
