// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

/// @notice Reads the vault's HyperCore perp-account equity (margin + unrealized
/// PnL) for NAV/share pricing, denominated in the vault asset's units (USDC, 6dp).
/// @dev Implemented over HyperCore read precompiles. Kept behind an interface so
/// the (still-to-be-verified) precompile ABIs are isolated from the vault logic
/// and can be corrected/audited independently.
interface IHyperCoreReader {
    function accountEquityUsd(address account) external view returns (uint256);

    /// @notice USDC sitting in the account's HyperCore *spot* sub-account (e.g.
    /// parked mid-bridge between EVM and the perp margin account), denominated in
    /// the vault asset's units (USDC, 6dp). Disjoint from {accountEquityUsd},
    /// which reads the perp margin account — the two never overlap, so a NAV that
    /// sums both cannot double-count.
    function spotBalanceUsd(address account) external view returns (uint256);
}
