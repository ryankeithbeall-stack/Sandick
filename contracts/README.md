# Sandick Vault — on-chain (HyperEVM) contracts

A **trustless, tokenized vault** on Hyperliquid's **HyperEVM** that custodies USDC
and trades an equal-weighted HIP-3 basket on **HyperCore** (via the Trade.xyz
builder dex) through the **CoreWriter** system contract.

> ⚠️ **UNAUDITED — not for mainnet funds.** The accounting/trust logic is tested,
> but the HyperCore integration has unverified inputs (see below) and the whole
> system needs an audit + testnet sign-off before holding real deposits.

## Trust model

- Depositors deposit USDC and receive transferable ERC-20 (ERC-4626) shares.
- The **vault contract custodies all funds** and is itself the HyperCore trading
  account (CoreWriter attributes actions to `msg.sender` = the contract).
- Assets leave only via `withdraw`/`redeem` (sync, capped to idle liquidity) or
  the **async redemption queue** (`requestRedeem` → `fulfillRedeem` → `claim`),
  always pro-rata to shares.
- The **manager** (strategy key) may only (a) submit orders on **allow-listed**
  assets and (b) move funds between the vault's *own* HyperEVM/HyperCore
  balances. It can never transfer funds to itself. Worst-case manager abuse is
  bad trading, not theft. This is enforced by the absence of any fund-exit path
  and verified by a test.

## Layout

```
src/
  SandickVaultBase.sol        # ERC-4626 + roles + NAV + withdrawal caps (abstract)
  SandickVault.sol            # production: CoreWriter write-path + reader NAV
  HyperCoreReader.sol         # NAV via accountMarginSummary precompile (0x..080F)
  lib/HyperCoreActions.sol    # CoreWriter action encodings (confirmed)
  interfaces/                 # ICoreWriter, IHyperCoreReader
test/
  vault.test.js               # in-process EVM tests (ethereumjs)
  mocks/                      # MockERC20, MockCore, MockSandickVault
```

## What's confirmed vs must-verify

**Confirmed** (against `hyperliquid-dev/hyper-evm-lib` + official docs):
- CoreWriter at `0x3333…3333`, `sendRawAction(bytes)`.
- Payload = `abi.encodePacked(uint8(1), uint24(actionId), abi.encode(args))`.
- Limit order = action 1 `(uint32 asset,bool isBuy,uint64 limitPx,uint64 sz,bool reduceOnly,uint8 tif,uint128 cloid)`; px/sz × 1e8; tif 1=ALO/2=GTC/3=IOC.
- USD class transfer = action 7 `(uint64 ntl, bool toPerp)`; spot send = action 6.
- HIP-3 asset id = `100000 + perp_dex_index*10000 + index_in_meta` (first builder dex → 110000).
- CoreWriter is **async + fails silently** (no revert); fund the Core account in an **earlier block** than the first trade.

NAV reading is implemented: `HyperCoreReader` staticcalls the
`accountMarginSummary` precompile (`0x..080F`) and uses `accountValue`
(collateral + uPnL, already 6-decimal USDC) — confirmed against hyper-evm-lib.
The precompile address is an immutable (mockable in tests, fixable on-chain).

**Must verify on testnet before mainnet** (externalized to constructor immutables
so nothing unconfirmed is hard-coded as fact):
- The `perpDexIndex` for the Trade.xyz dex, and behavior of `accountMarginSummary`
  for a never-initialized Core account (seed the account before opening deposits).
  USDC parked in spot mid-bridge is not counted — extend with the spot-balance
  precompile if needed.
- USDC's exact system address (`0x20…<tokenIndex>`), Core token index, and the
  EVM↔Core decimal scale (`coreScale`) — read from live `spotMeta`.
## Async redemption queue

Because CoreWriter is asynchronous, the vault cannot unwind HyperCore positions
inside a `withdraw` call. Two exit paths:

- **Sync** `withdraw`/`redeem` — capped to idle (unreserved) HyperEVM liquidity.
- **Async** for larger exits:
  1. `requestRedeem(shares)` escrows the shares in the vault.
  2. The manager unwinds positions and `bridgeFromCore`s USDC over later blocks.
  3. `fulfillRedeem(owner, shares)` — **permissionless** once idle funds exist —
     prices the shares at the *current* NAV, burns them, and reserves the USDC.
  4. `claim()` pays out the reserved USDC.

Shares are priced at **fulfillment**, so a redeemer bears market moves until
funds are available (not the remaining holders). Reserved assets are excluded
from NAV and protected from sync withdrawals. Fulfillment being permissionless
means the manager can never block an exit once liquidity is present.

### Redemption-liveness backstop

Step 2 above relies on the manager bridging funds back. If the manager key goes
**dark**, that step never happens and queued exits could starve. The backstop
closes that gap: once the manager has been inactive for `managerTimeout` (default
7 days; owner-settable; 0 disables), **anyone** may call
`bridgeFromCoreForRedemptions(amount)` to pull USDC from Core back to the vault —
but only up to `redemptionDeficit()` (the USDC owed to the queue beyond idle
liquidity). It never moves funds out of the vault and never pulls more than is
owed, so it can't be used to grief the strategy; it only guarantees that a dark
manager can *delay* exits, never *trap* them. Any manager trade/bridge resets the
countdown (`managerIsDark()` exposes the current state).

## Owner controls (defense in depth)

The owner holds bounded, trust-minimized levers — none can seize deposits or
block exits:

- **Pause** (`pause`/`unpause`) — halts deposits/mints and manager trading
  (`submitBasket`, `bridgeToCore`). Exits (withdraw, redeem, the async queue,
  `claim`, `bridgeFromCore`) stay open, so a pause can never trap funds.
- **Order notional caps** (`setOrderCaps`) — bound the raw notional
  (`limitPx * sz`) of any single order leg and the cumulative notional submitted
  within a rolling epoch, limiting how hard a compromised manager key can churn
  the book before rotation/pause. A cap of 0 disables that check.
- **Manager rotation** (`setManager`) and **allow-list** (`setAllowedAsset`).

NAV (`totalAssets`) also counts USDC sitting in the Core **spot** account
mid-bridge via the `_coreSpotUsd()` hook (default 0; override wires the
spot-balance precompile), keeping share price continuous across the bridge.

## Fees

Three fees, all owner-configured within hard caps (`setFeeConfig`; mgmt ≤ 5%,
perf ≤ 30%, exit ≤ 1%), defaulting to **2%/yr management · 10% performance · 0.1%
exit**:

- **Management** — streams on NAV pro-rata to time.
- **Performance** — a cut of any gain in price-per-share above a global
  **high-water mark** (so it's only charged on new highs, never on recovery).
- **Exit** — a small fee on redemption, **retained in the vault** so it accrues
  to the holders who stay (and discourages churn / queue gaming).

Management + performance fees are minted as **dilution shares** to the treasury
(`feeRecipient`) rather than paid in USDC — so the *no funds ever leave the
vault* invariant is untouched; the treasury is just another share holder.
`_accrueFees()` runs before every deposit/withdraw/redeem and queue action
(and via the permissionless `accrueFees()` poke) so share price is always
fee-current. **Audit focus:** the performance fee keys off on-chain NAV, so the
NAV read must not be transiently inflatable.

## Build & test (no Foundry required)

```bash
npm install
npm run compile             # solc compile-check
npm run test:contracts      # in-process EVM tests (ethereumjs)
npm run coverage:contracts  # line coverage report (writes coverage/contracts/)
```

Tests run the real compiled bytecode on `@ethereumjs/vm`: deposits/shares,
NAV after bridging, withdrawal liquidity caps, PnL → share price, the trade-only
manager restrictions, the async redemption queue, and the pause + order-cap
owner controls.

### Coverage

`npm run coverage:contracts` re-runs the suite with the EVM `step` event hooked,
maps each executed program counter back to a source line via solc's deployed
source map, and prints per-file line coverage (writing `coverage.json` +
`lcov.info` under `coverage/contracts/`). Set `COVERAGE_MIN` to fail under a
threshold (CI gates the total at 65%).

The report is honest about where the tests stop: the trust/accounting core
(`SandickVaultBase.sol`) is ~96% covered, but the production HyperCore write-path
(`SandickVault.sol`, `lib/HyperCoreActions.sol`) is exercised only through mocks
and stays low until the testnet round-trip lands — see the root `TODO.md`.
