// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {BasketVaultBase} from "../../src/BasketVaultBase.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {MockCore} from "./MockCore.sol";

/// @dev Concrete vault wired to {MockCore} instead of real CoreWriter/precompiles,
/// so the trust + accounting logic can be unit-tested deterministically.
///
/// The convenience constructor defaults the platform fee OFF (protocolAdmin and
/// treasury = owner, fee = 0) so the base accounting tests are unaffected; the
/// protocol-fee tests turn it on via `setProtocolFeeConfig`.
contract MockBasketVault is BasketVaultBase {
    MockCore public immutable core;

    // Recorded order legs, for assertions.
    Order[] public submitted;

    constructor(
        IERC20 asset_,
        address manager_,
        address owner_,
        MockCore core_
    )
        BasketVaultBase(
            asset_,
            "Basket Vault",
            "bVLT",
            manager_,
            owner_,
            owner_, // protocolAdmin defaults to the owner in the mock
            owner_, // protocolTreasury
            0 // platform fee off by default
        )
    {
        core = core_;
    }

    function submittedCount() external view returns (uint256) {
        return submitted.length;
    }

    function _coreEquityUsd() internal view override returns (uint256) {
        return core.equity(address(this));
    }

    function _submitOrder(Order calldata order) internal override {
        submitted.push(order);
    }

    function _bridgeToCore(uint256 amount) internal override {
        IERC20(asset()).approve(address(core), amount);
        core.deposit(amount);
    }

    function _bridgeFromCore(uint256 amount) internal override {
        core.withdraw(address(this), amount);
    }
}
