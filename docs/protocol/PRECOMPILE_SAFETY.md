# Precompile Safety — Aperture's live NAV read path

Aperture has **no manual NAV oracle**. A `BasketVault` prices its shares directly
off live HyperCore state via a read precompile. This doc is the operator/auditor
reference for that read path: where the trust lives, what can make a read stale or
wrong, and how the off-chain keeper compensates without ever becoming a NAV
authority.

Source files:
- `contracts/src/HyperCoreReader.sol` — the precompile staticcall.
- `contracts/src/interfaces/IHyperCoreReader.sol` — the one-method interface.
- `contracts/src/BasketVaultBase.sol` — `totalAssets()` and the `_core*` hooks.
- `contracts/src/BasketVault.sol` — wires the reader into `_coreEquityUsd()`.

Related: [`COREWRITER_ACTION_MATRIX.md`](COREWRITER_ACTION_MATRIX.md),
[`../../GO-LIVE.md`](../../GO-LIVE.md) (blocker #8: NAV completeness),
[`../testnet-signoff.md`](../testnet-signoff.md), [`../security/README.md`](../security/README.md).

## NAV composition

`BasketVaultBase.totalAssets()` is the sum of three live components — no stored,
operator-poked, or signed value enters the calculation:

```
totalAssets() = _idleAssets() + _coreEquityUsd() + _coreSpotUsd()
```

| Component | Source | Notes |
|---|---|---|
| `_idleAssets()` | `USDC.balanceOf(vault) - reservedAssets` | On-EVM, fully trustless. Excludes USDC already reserved for queued claims. |
| `_coreEquityUsd()` | `reader.accountEquityUsd(address(this))` → the margin-summary precompile | Perp collateral + unrealized PnL. **This is the precompile-trusted leg.** |
| `_coreSpotUsd()` | virtual hook, **defaults to 0** | The known mid-bridge NAV gap (see below). |

## The `accountMarginSummary` precompile (0x…080F)

`HyperCoreReader` holds the precompile address as an **immutable**
(`marginSummaryPrecompile`, production `0x…080F`) plus an immutable
`perpDexIndex`. The single hot path:

```solidity
(bool ok, bytes memory res) =
    marginSummaryPrecompile.staticcall(abi.encode(perpDexIndex, account));
if (!ok || res.length < 128) revert MarginSummaryReadFailed();
(int64 accountValue,,,) = abi.decode(res, (int64, uint64, uint64, int64));
return accountValue <= 0 ? 0 : uint256(uint64(accountValue));
```

The decoded tuple is `(int64 accountValue, uint64 marginUsed, uint64 ntlPos,
int64 rawUsd)`; only `accountValue` (collateral + uPnL, already in 6-decimal USDC
units, matching the vault asset 1:1) is consumed. The address is an immutable
specifically so it can point at a mock in tests and be re-pointed without
redeploying the vault if Hyperliquid moves it.

### Hazard 1 — all-gas-on-invalid-input

HyperCore read precompiles **consume all forwarded gas on malformed input**
rather than returning a clean revert. Two consequences the reader is built around:

- The encoded argument shape **must** be exactly `abi.encode(uint32 perpDexIndex,
  address user)`. A wrong `perpDexIndex` (the value is unverified for the
  Trade.xyz builder dex — see GO-LIVE.md blocker #2/#5) is the most likely
  trigger; this is an `immutable` so a bad value is fixed by redeploying the
  reader, not by patching state.
- `totalAssets()` is `view` and is called inside `deposit`/`withdraw`/`redeem`/
  `fulfillRedeem`/`_accrueFees`. A reverting/gas-burning read therefore **blocks
  those calls**. By design this fails *safe* for share-price integrity (no
  mispriced mint/redeem can sneak through) but it can wedge user actions, so the
  precompile address + `perpDexIndex` are part of the testnet sign-off gate, not
  a runtime variable.

### Hazard 2 — start-of-block staleness

The margin-summary read reflects **HyperCore state as of the start of the EVM
block**, and CoreWriter actions settle asynchronously on *later* Core blocks
(see the action matrix). So a NAV read can lag reality:

- After `bridgeToCore` / `submitBasket` / `bridgeFromCore`, the equity reflected
  by the precompile updates only once Core has processed the action — possibly
  several blocks later.
- A deposit/redeem priced in the same block as an in-flight CoreWriter action is
  priced against the *pre-action* equity.

There is **no on-chain freshness check** today — `_coreEquityUsd()` trusts
whatever the precompile returns for the current block. GO-LIVE.md blocker #8
tracks adding explicit stale-read handling. Until then, freshness is enforced
off-chain (next section).

### Hazard 3 — underwater clamp-to-0

If the perp account is underwater/liquidated, `accountValue` is negative. The
reader clamps it: `accountValue <= 0 ? 0 : …`. This keeps `totalAssets()`
monotonic into share pricing (a vault can't price shares off negative equity) but
note the implication: **a clamped read hides the magnitude of a loss** —
`_coreEquityUsd()` reports `0`, not "−$X". NAV floors at idle USDC. This is
correct for ERC-4626 (shares can't be worth less than zero) but means the
on-chain number alone won't tell an operator *how far* underwater the position
is; that has to come from the raw precompile / off-chain reads.

### Hazard 4 — the `_coreSpotUsd()` mid-bridge gap

`_coreSpotUsd()` is a virtual hook that **returns 0 by default** and is not yet
overridden. USDC bridged between the EVM, the Core **spot** account, and the perp
**margin** account is multi-step and async (`_bridgeToCore` = ERC-20 send to the
system address → `usdClassTransfer` spot→perp; `_bridgeFromCore` is the reverse).
While funds sit in the Core spot account mid-bridge they are counted by **neither**
`_idleAssets()` (already left the EVM) **nor** `_coreEquityUsd()` (not yet perp
margin). For that window NAV **understates** the vault, so share price dips and
recovers across the bridge. A depositor minting in that window gets a slightly
favorable price; a redeemer a slightly unfavorable one.

Mitigations: this is a *known, bounded* gap, kept small by bridging in modest
tranches; closing it is GO-LIVE.md blocker #8 (wire the spot-balance precompile
into `_coreSpotUsd()` so in-flight USDC counts). **Do not** introduce a manual
NAV value to paper over it — Aperture is trustless-NAV by construction.

## Off-chain compensation (keeper, not an oracle)

The keeper never writes NAV on-chain; it reads the same live values and refuses to
act when they look wrong. Two layers in `sandick/`:

- **Fail-closed pre-tick gate** — `keeper_guard.evaluate_gate(KeeperState)` runs
  *first* every tick (`keeper_bot.tick`). It is pure arithmetic over the reads and
  returns `blockers`; if non-empty the bot **refuses to act**. It catches exactly
  the precompile failure modes above as they surface in the reads: any of
  `nav / idle / pending_redeem / core_available` negative, a missing/non-positive
  price for an open position, `idle > nav` (idle is a *component* of NAV, so
  strictly greater is impossible — a contradictory read), or queued redemptions
  with `nav == 0`. A brand-new empty vault (`nav == 0`, nothing queued) is allowed.
- **Read-act-verify** — `keeper_bot.tick` reads state, acts, then **re-reads to
  verify** (e.g. `_verify_idle_rose` after a `bridge_from_core`,
  `_verify_drift_cleared` after a rebalance). Because CoreWriter is async and
  silent, an action that didn't settle simply isn't confirmed and is flagged
  unverified rather than assumed done. This directly compensates for Hazard 2:
  the keeper never builds on an unsettled action.

Both layers are belt-and-suspenders on top of the on-chain invariants — neither
can move funds, set NAV, or override the precompile. They only decide whether the
keeper *acts*. Live broadcasts are additionally hard-gated by `ALLOW_LIVE_TX=1`
(`sandick/safety.py`), and `keeper_cli --health-out` emits a machine-readable
snapshot that exits non-zero when unhealthy (GO-LIVE.md step #6).

## Auditor checklist

- [ ] Confirm `perpDexIndex` and the `0x…080F` address for the Trade.xyz dex on
      testnet (chainid 998); a wrong value burns all gas, not a clean revert.
- [ ] Document fresh/never-initialized Core-account behavior (revert vs zeros) and
      seed the account before opening deposits (GO-LIVE.md blocker #2).
- [ ] Confirm the underwater clamp can't be exploited to *inflate* NAV (it only
      floors it) — and that the performance fee, which keys off NAV, can't be
      transiently inflated through a stale/uninitialized read.
- [ ] Decide whether the `_coreSpotUsd()=0` gap is acceptable at launch or must be
      closed first (GO-LIVE.md blocker #8).
