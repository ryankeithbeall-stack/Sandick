// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

/// @dev Stand-in for the HyperCore spotBalance precompile (0x..0801). Returns the
/// SpotBalance tuple (uint64 total, uint64 hold, uint64 entryNtl) for any raw
/// staticcall, reading `total` from storage (in HyperCore spot-wei units, e.g.
/// 8dp for USDC) so tests can vary the parked balance. `hold`/`entryNtl` are 0.
contract MockSpotBalance {
    uint64 public total;

    function setTotal(uint64 v) external {
        total = v;
    }

    fallback(bytes calldata) external returns (bytes memory) {
        return abi.encode(total, uint64(0), uint64(0));
    }
}
