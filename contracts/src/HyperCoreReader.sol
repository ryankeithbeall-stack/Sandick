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
/// MUST VERIFY ON TESTNET (chainid 998):
///   * `perpDexIndex` for the Trade.xyz builder dex (default dex = 0).
///   * Behavior for a never-initialized Core account (revert vs zeros) — seed the
///     vault's Core account before opening deposits so this read succeeds.
///   * That USDC parked in spot mid-bridge (not in the perp account) is acceptable
///     to omit from NAV, or extend this reader with the spot-balance precompile.
contract HyperCoreReader is IHyperCoreReader {
    /// @notice The accountMarginSummary precompile (production: 0x..080F).
    address public immutable marginSummaryPrecompile;

    /// @notice Perp dex index to read equity for (0 = default; Trade.xyz = its index).
    uint32 public immutable perpDexIndex;

    error MarginSummaryReadFailed();

    constructor(address marginSummaryPrecompile_, uint32 perpDexIndex_) {
        require(marginSummaryPrecompile_ != address(0), "zero precompile");
        marginSummaryPrecompile = marginSummaryPrecompile_;
        perpDexIndex = perpDexIndex_;
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
}
