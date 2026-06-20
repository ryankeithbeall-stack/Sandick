// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

/// @notice Reads the vault's HyperCore perp-account equity (margin + unrealized
/// PnL) for NAV/share pricing, denominated in the vault asset's units (USDC, 6dp).
/// @dev Implemented over HyperCore read precompiles. Kept behind an interface so
/// the (still-to-be-verified) precompile ABIs are isolated from the vault logic
/// and can be corrected/audited independently.
interface IHyperCoreReader {
    function accountEquityUsd(address account) external view returns (uint256);
}
