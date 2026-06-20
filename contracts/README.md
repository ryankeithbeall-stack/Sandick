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
- The **only** way assets leave is `withdraw`/`redeem`, paid pro-rata to shares.
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
  HyperCoreReader.sol         # NAV via read precompiles  (STUB — to implement)
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

**Must verify on testnet before mainnet** (externalized to constructor immutables
/ the reader stub so nothing unconfirmed is hard-coded as fact):
- `HyperCoreReader.accountEquityUsd` — the read-precompile addresses/ABIs and the
  equity computation (critical: NAV must be on-chain, never manager-set).
- USDC's exact system address (`0x20…<tokenIndex>`), Core token index, and the
  EVM↔Core decimal scale (`coreScale`) — read from live `spotMeta`.
- That the async withdrawal model (currently: cap to idle liquidity) is replaced
  by a proper request/settle redemption queue.

## Build & test (no Foundry required)

```bash
npm install
npm run compile        # solc compile-check (28 contracts)
npm run test:contracts # in-process EVM tests (ethereumjs) — 7 passing
```

Tests run the real compiled bytecode on `@ethereumjs/vm`: deposits/shares,
NAV after bridging, withdrawal liquidity caps, PnL → share price, and the
trade-only manager restrictions.
