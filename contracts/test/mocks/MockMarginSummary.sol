// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

/// @dev Stand-in for the HyperCore accountMarginSummary precompile. Returns the
/// confirmed tuple (int64 accountValue, uint64 marginUsed, uint64 ntlPos,
/// int64 rawUsd) for any raw staticcall, reading accountValue from storage so
/// tests can vary it.
contract MockMarginSummary {
    int64 public accountValue;

    function setAccountValue(int64 v) external {
        accountValue = v;
    }

    fallback(bytes calldata) external returns (bytes memory) {
        return abi.encode(accountValue, uint64(0), uint64(0), int64(0));
    }
}
