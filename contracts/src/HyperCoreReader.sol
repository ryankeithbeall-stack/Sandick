// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {IHyperCoreReader} from "./interfaces/IHyperCoreReader.sol";

/// @title HyperCoreReader
/// @notice NAV reader over the HyperCore `accountMarginSummary` read precompile.
///
/// The margin-summary precompile (mainnet/testnet address 0x..080F) returns, for
/// `abi.encode(uint32 perpDexIndex, address user)`, the struct:
///   (int64 accountValue, uint64 marginUsed, uint64 ntlPos, int64 rawUsd)
/// where `accountValue` = collateral + unrealized PnL, already in 6-decimal USDC
/// units — the same decimals as the vault's USDC asset, so it feeds totalAssets()
/// 1:1. Confirmed against hyperliquid-dev/hyper-evm-lib (PrecompileLib.sol,
/// HLConstants.sol).
///
/// The precompile address is an immutable so it can be pointed at a mock in tests
/// and corrected without redeploying the vault.
///
/// The spot-balance read uses a SECOND precompile (production: 0x..0801), which
/// returns the SpotBalance struct (uint64 total, uint64 hold, uint64 entryNtl) for
/// `abi.encode(address user, uint64 token)` — note the input order is the REVERSE
/// of accountMarginSummary (address first, token second). Spot balances come back
/// in the token's HyperCore *wei* decimals (USDC = 8dp), NOT the 6dp the margin
/// summary uses, so they are scaled down by `spotWeiToAssetDivisor` (= 10**(8-6) =
/// 100) to land in the vault asset's units. That divisor is numerically the same
/// value as `BasketVault.coreScale` (the EVM->Core write-path multiplier) applied
/// inversely on the Core->EVM read path; deployments MUST set them from one source
/// (see deploy.js) so they cannot diverge.
///
/// MUST VERIFY ON TESTNET (chainid 998):
///   * `perpDexIndex` for the Trade.xyz builder dex (default dex = 0).
///   * Behavior for a never-initialized Core account (revert vs zeros) for BOTH
///     precompiles — seed the vault's Core perp+spot accounts before opening
///     deposits so these reads succeed (a revert in either bricks totalAssets()).
///   * The spot-balance precompile address (0x..0801), its input ABI order
///     (address, uint64), its (uint64,uint64,uint64) return, and USDC's
///     `weiDecimals` (expected 8) so `spotWeiToAssetDivisor` == `coreScale`.
///   * The USDC<->Core bridge decimal convention: `usdClassTransfer` (perp ntl)
///     and `spotSend` (spot wei) may use DIFFERENT decimals; confirm the amounts
///     BasketVault passes are correct end-to-end, since this reader makes any
///     mis-scaled spot balance visible in NAV. (See GO-LIVE.md step 8.)
contract HyperCoreReader is IHyperCoreReader {
    /// @notice The accountMarginSummary precompile (production: 0x..080F).
    address public immutable marginSummaryPrecompile;

    /// @notice Perp dex index to read equity for (0 = default; Trade.xyz = its index).
    uint32 public immutable perpDexIndex;

    /// @notice The spotBalance precompile (production: 0x..0801).
    address public immutable spotBalancePrecompile;

    /// @notice HyperCore spot token index for USDC (mainnet canonical = 0). Must
    /// match `BasketVault.usdcCoreTokenIndex` used by spot-send.
    uint64 public immutable usdcCoreTokenIndex;

    /// @notice Divisor converting a HyperCore spot-wei USDC balance (weiDecimals,
    /// e.g. 8) down to the vault asset's USDC units (6dp): 10**(weiDecimals - 6).
    /// Numerically equals `BasketVault.coreScale`; set from the same source.
    uint256 public immutable spotWeiToAssetDivisor;

    error MarginSummaryReadFailed();
    error SpotBalanceReadFailed();

    constructor(
        address marginSummaryPrecompile_,
        uint32 perpDexIndex_,
        address spotBalancePrecompile_,
        uint64 usdcCoreTokenIndex_,
        uint256 spotWeiToAssetDivisor_
    ) {
        require(marginSummaryPrecompile_ != address(0), "zero precompile");
        require(spotBalancePrecompile_ != address(0), "zero spot precompile");
        require(spotWeiToAssetDivisor_ > 0, "zero scale");
        marginSummaryPrecompile = marginSummaryPrecompile_;
        perpDexIndex = perpDexIndex_;
        spotBalancePrecompile = spotBalancePrecompile_;
        usdcCoreTokenIndex = usdcCoreTokenIndex_;
        spotWeiToAssetDivisor = spotWeiToAssetDivisor_;
    }

    /// @inheritdoc IHyperCoreReader
    function accountEquityUsd(address account) external view returns (uint256) {
        (bool ok, bytes memory res) =
            marginSummaryPrecompile.staticcall(abi.encode(perpDexIndex, account));
        if (!ok || res.length < 128) revert MarginSummaryReadFailed();
        (int64 accountValue,,,) = abi.decode(res, (int64, uint64, uint64, int64));
        // Negative equity (underwater/liquidated) clamps to 0 for share pricing.
        return accountValue <= 0 ? 0 : uint256(uint64(accountValue));
    }

    /// @inheritdoc IHyperCoreReader
    function spotBalanceUsd(address account) external view returns (uint256) {
        // Input order is (address, uint64) — the REVERSE of the margin summary's
        // (uint32, address). Do not copy that ordering by reflex.
        (bool ok, bytes memory res) =
            spotBalancePrecompile.staticcall(abi.encode(account, usdcCoreTokenIndex));
        // SpotBalance = (uint64 total, uint64 hold, uint64 entryNtl) => 96 bytes.
        // A failed/short read reverts (never silently 0 — that would misprice NAV).
        if (!ok || res.length < 96) revert SpotBalanceReadFailed();
        (uint64 total,,) = abi.decode(res, (uint64, uint64, uint64));
        // Count `total`, not total-minus-hold: held USDC is still vault-owned
        // equity (this vault places no spot orders, so hold is 0 in practice, but
        // counting total is the correct NAV figure regardless). Scale spot-wei
        // (8dp) down to the asset's 6dp; integer division truncates toward zero,
        // which is conservative (never over-reports NAV) and dust-negligible.
        return uint256(total) / spotWeiToAssetDivisor;
    }
}
