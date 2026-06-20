// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

/// @notice Minimal interface for the HyperEVM CoreWriter system contract, which
/// forwards encoded actions (orders, transfers) to HyperCore.
/// @dev Address and payload encoding are filled in by the concrete adapter once
/// confirmed against Hyperliquid's canonical spec.
interface ICoreWriter {
    function sendRawAction(bytes calldata data) external;
}
