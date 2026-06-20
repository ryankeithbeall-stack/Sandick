// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @dev Stand-in for HyperCore in tests: custodies bridged USDC per account and
/// lets tests simulate PnL by adjusting equity. Models that bridged funds leave
/// the EVM contract entirely.
contract MockCore {
    IERC20 public immutable usdc;
    mapping(address => uint256) public equity;

    constructor(IERC20 _usdc) {
        usdc = _usdc;
    }

    /// @dev Caller (the vault) bridges `amt` into its own Core account.
    function deposit(uint256 amt) external {
        usdc.transferFrom(msg.sender, address(this), amt);
        equity[msg.sender] += amt;
    }

    /// @dev Caller bridges `amt` back to `to` on the EVM side.
    function withdraw(address to, uint256 amt) external {
        equity[msg.sender] -= amt;
        usdc.transfer(to, amt);
    }

    /// @dev Test helper: simulate trading PnL. To realize gains on withdrawal,
    /// the test should also `fund` the extra USDC.
    function setEquity(address acct, uint256 e) external {
        equity[acct] = e;
    }

    function fund(uint256 amt) external {
        usdc.transferFrom(msg.sender, address(this), amt);
    }
}
