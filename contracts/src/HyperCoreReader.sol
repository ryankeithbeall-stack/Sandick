// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {IHyperCoreReader} from "./interfaces/IHyperCoreReader.sol";

/// @title HyperCoreReader
/// @notice NAV reader over HyperCore read precompiles.
///
/// !!! NOT YET IMPLEMENTED / UNVERIFIED !!!
/// This is the single remaining integration point. NAV correctness is what makes
/// share pricing trustless, so it MUST read on-chain precompile state (never a
/// manager-set value). The precompile addresses and return ABIs were not
/// confirmable from a primary source in this build and MUST be verified against
/// the live docs + testnet before use. It intentionally reverts so it can never
/// be mistaken for working code on mainnet.
///
/// Intended implementation (HyperCore read precompiles, ~0x0000..0800 range):
///   * perp position per asset (size, entry, unrealized PnL)
///   * perp account margin summary / withdrawable
///   * oracle/mark price per asset
/// Equity = perp account value (collateral + Σ unrealized PnL), scaled from
/// HyperCore integer units to the vault asset's 6 decimals.
contract HyperCoreReader is IHyperCoreReader {
    error NotImplemented();

    function accountEquityUsd(address) external pure returns (uint256) {
        revert NotImplemented();
    }
}
