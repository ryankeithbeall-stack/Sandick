# CoreWriter Action Matrix — what Aperture wires vs. what it deliberately doesn't

Every HyperCore action a `BasketVault` can take goes through one place:
`contracts/src/lib/HyperCoreActions.sol`, which calls
`ICoreWriter(0x3333…3333).sendRawAction(bytes)`. This doc enumerates the CoreWriter
action space and marks each as **wired** (encoded by `HyperCoreActions`) or
**out of scope** (intentionally not encoded — there is no code path to it). For an
auditor, "out of scope" means: a compromised manager key cannot reach it, because
the bytes are never constructed.

Source files: `contracts/src/lib/HyperCoreActions.sol`,
`contracts/src/BasketVault.sol` (callers), `contracts/src/BasketVaultBase.sol`
(`Order` struct, caps, reduce-only mode), `contracts/README.md`.

Related: [`PRECOMPILE_SAFETY.md`](PRECOMPILE_SAFETY.md) (the read side),
[`../../GO-LIVE.md`](../../GO-LIVE.md), [`../testnet-signoff.md`](../testnet-signoff.md).

## Wire format

```
payload = abi.encodePacked(uint8(1) version, uint24(actionId), abi.encode(args))
CoreWriter = 0x3333333333333333333333333333333333333333
```

Confirmed against `hyperliquid-dev/hyper-evm-lib` (`CoreWriterLib.sol`,
`HLConstants.sol`) and the official "Interacting with HyperCore" docs.

## Action matrix

| Action | ID | Status | Where | Args / notes |
|---|---|---|---|---|
| **Limit order** | 1 | **WIRED** | `HyperCoreActions.limitOrder` ← `BasketVault._submitOrder` ← `submitBasket` | `(uint32 asset, bool isBuy, uint64 limitPx, uint64 sz, bool reduceOnly, uint8 tif, uint128 cloid)`; `limitPx`/`sz` are × `1e8` (`PX_SZ_SCALE`); `tif` ∈ {1 ALO, 2 GTC, 3 IOC}; vault passes `cloid = 0`. |
| **USD class transfer** | 7 | **WIRED** | `HyperCoreActions.usdClassTransfer` ← `_bridgeToCore` / `_bridgeFromCore` | `(uint64 ntl, bool toPerp)`; moves USDC between the vault's own spot ↔ perp sub-accounts. |
| **Spot send** | 6 | **WIRED** | `HyperCoreActions.spotSend` ← `_bridgeFromCore` | `(address to, uint64 token, uint64 amountWei)`; used to bridge Core→EVM by sending to USDC's system address (`0x20…<tokenIndex>`). |
| Order cancel (by oid) | 10 | **OUT OF SCOPE** | — | Not encoded. **There is no on-chain cancel.** See "No cancel" below. |
| Order cancel (by cloid) | 11 | **OUT OF SCOPE** | — | Not encoded. Same as above. |
| Update leverage | — | **OUT OF SCOPE** | — | Not encoded; leverage is whatever Core defaults to for the dex/asset. |
| Update isolated margin | — | **OUT OF SCOPE** | — | Not encoded; no per-position margin adjustment from the contract. |
| Modify order | — | **OUT OF SCOPE** | — | Not encoded; a "modify" is expressed as new reduce-only legs, not an amend. |
| Schedule cancel (dead-man) | — | **OUT OF SCOPE** | — | Not encoded; no auto-cancel timer. |
| Vault transfer / deposit | — | **OUT OF SCOPE** | — | Not encoded; the vault is the trading account itself and never deposits into another Core vault. |
| Staking / delegation | — | **OUT OF SCOPE** | — | Not encoded; out of the basket-perp mandate. |
| Token delegate / approve, etc. | — | **OUT OF SCOPE** | — | Not encoded; any action absent from `HyperCoreActions` is unreachable. |

> Only actions **1, 7, 6** have an encoder. Everything else has no callable path
> from any role, by construction. This is the on-chain half of the trust model: the
> manager can submit orders and shuffle the vault's *own* funds between its sub-
> accounts, and nothing else.

## Two properties auditors must internalize

### CoreWriter is async + silent-failure

`HyperCoreActions._send` calls `sendRawAction` and **returns nothing**. CoreWriter:

- does **not** revert if the Core action fails (insufficient margin, invalid
  asset, account not funded yet) — it just silently no-ops;
- intentionally delays order/vault actions a few seconds, executing on a **later
  Core block**.

Therefore: never assume an order placed here filled, and **funds must arrive on an
earlier block than the action that uses them**. Concretely, `bridgeToCore` must
land (and settle on Core) *before* the `submitBasket` that relies on that margin;
issuing both in the same tick risks the order silently failing for lack of margin.
Confirmation is the reader's job, off-chain — see
[`PRECOMPILE_SAFETY.md`](PRECOMPILE_SAFETY.md) and the keeper's read-act-verify
loop (`sandick/keeper_bot.tick`).

### No cancel → flatten via reduce-only legs

Because actions **10/11 are not wired** (and CoreWriter can't be made to revert),
**a resting order cannot be cancelled on-chain.** To reduce or flatten exposure
the vault submits **reduce-only legs** through the normal
`submitBasket(Order[])` path (`Order.reduceOnly = true`,
`HyperCoreActions.limitOrder(..., reduceOnly, ...)`). The platform's emergency
levers are built around this constraint:

- **`reduceOnlyMode`** (guardian-or-owner via `setReduceOnlyMode`): manager orders
  *must* be reduce-only and `bridgeToCore` is blocked — a wind-down state that lets
  the manager unwind but not add risk, while exits stay open.
- **`requirePostOnly`** (owner via `setRequirePostOnly`): forces ALO/maker orders.
  Note this is deliberately **not** an emergency lever — forcing ALO would block a
  crisis unwind that must cross the book.
- **Order caps** (`setOrderCaps` global, `setAssetOrderCap` per-asset): bound a
  single leg's raw notional (`limitPx * sz`) and the rolling-epoch cumulative
  notional, limiting churn by a compromised manager key.

None of these can move funds out of the vault; they only constrain the bytes the
manager is allowed to emit.

## HIP-3 asset-id formula

`limitOrder`'s `asset` is a HyperCore asset id. For HIP-3 (builder-dex) perps:

```
assetId = 100000 + perpDexIndex * 10000 + indexInMeta
```

(the first builder dex → base `110000`). `perpDexIndex` and the per-coin
`indexInMeta` for the Trade.xyz dex are **placeholders pending live verification**
(GO-LIVE.md blockers #2 and #5 — resolve from the live `meta`; do not hardcode
guesses). The same `perpDexIndex` must match the one compiled into
`HyperCoreReader` (see [`PRECOMPILE_SAFETY.md`](PRECOMPILE_SAFETY.md)), or the read
and write paths point at different dexes.

## Auditor checklist

- [ ] Confirm no encoder exists for any action outside {1, 7, 6} — grep
      `HyperCoreActions.sol` for `_send` callers.
- [ ] Confirm the only on-chain "flatten" is reduce-only `submitBasket` legs, and
      that `reduceOnlyMode` correctly forces `reduceOnly` while keeping exits open.
- [ ] Confirm bridge ordering: margin in (`bridgeToCore`) settles before any
      `submitBasket` depends on it — async + silent failure makes this load-bearing.
- [ ] Verify the HIP-3 `assetId` (and matching `perpDexIndex`) on testnet before
      mainnet.
