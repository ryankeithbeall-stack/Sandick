// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {ERC4626} from "@openzeppelin/contracts/token/ERC20/extensions/ERC4626.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @title SandickVaultBase
/// @notice Trustless, tokenized HyperEVM vault for an equal-weighted HIP-3 basket.
///
/// Trust model:
///  * Depositors deposit USDC and receive transferable ERC-20 (ERC-4626) shares.
///  * The vault contract custodies all funds and is itself the HyperCore trading
///    account. The ONLY way assets leave the contract is `withdraw`/`redeem`,
///    paid pro-rata to share holders.
///  * The `manager` (strategy key) may ONLY trade an allow-listed set of assets
///    and move funds between the vault's own HyperEVM/HyperCore balances. It can
///    never transfer assets to itself or any third party. Worst-case manager
///    abuse is bad trading, not theft.
///
/// HyperCore integration (CoreWriter actions + read precompiles) is abstracted
/// behind the `_core*` hooks so the accounting/trust logic is testable against a
/// mock; the concrete implementation lives in {SandickVault}.
abstract contract SandickVaultBase is ERC4626, Ownable, ReentrancyGuard {
    /// @notice Strategy key permitted to trade (but never to move funds out).
    address public manager;

    /// @notice Assets (HyperCore asset ids) the manager is allowed to trade.
    mapping(uint32 => bool) public allowedAsset;

    /// @dev A single order leg. Prices/sizes are in HyperCore integer units; the
    /// off-chain planner produces these from the equal-weight plan.
    struct Order {
        uint32 assetId;
        bool isBuy;
        uint64 limitPx;
        uint64 sz;
        bool reduceOnly;
    }

    event ManagerUpdated(address indexed manager);
    event AssetAllowed(uint32 indexed assetId, bool allowed);
    event OrderSubmitted(uint32 indexed assetId, bool isBuy, uint64 limitPx, uint64 sz, bool reduceOnly);
    event BasketSubmitted(uint256 count);
    event BridgedToCore(uint256 amount);
    event BridgedFromCore(uint256 amount);

    error NotManager();
    error ZeroAddress();
    error AssetNotAllowed(uint32 assetId);

    modifier onlyManager() {
        if (msg.sender != manager) revert NotManager();
        _;
    }

    constructor(
        IERC20 asset_,
        string memory name_,
        string memory symbol_,
        address manager_,
        address owner_
    ) ERC20(name_, symbol_) ERC4626(asset_) Ownable(owner_) {
        if (manager_ == address(0) || owner_ == address(0)) revert ZeroAddress();
        manager = manager_;
        emit ManagerUpdated(manager_);
    }

    // --------------------------------------------------------------------- //
    //                                  NAV                                   //
    // --------------------------------------------------------------------- //

    /// @notice Vault NAV = idle USDC on HyperEVM + equity on HyperCore (margin +
    /// unrealized PnL), denominated in the underlying asset's units.
    function totalAssets() public view override returns (uint256) {
        return _idleAssets() + _coreEquityUsd();
    }

    /// @dev Inflation/donation-attack mitigation via virtual shares.
    function _decimalsOffset() internal pure override returns (uint8) {
        return 6;
    }

    function _idleAssets() internal view returns (uint256) {
        return IERC20(asset()).balanceOf(address(this));
    }

    // --------------------------------------------------------------------- //
    //                       Withdrawal liquidity caps                        //
    // --------------------------------------------------------------------- //
    // CoreWriter actions are asynchronous, so the vault cannot synchronously
    // unwind HyperCore positions inside a withdraw() call. Until the async
    // redemption queue lands, withdrawals are capped to idle HyperEVM liquidity
    // so ERC-4626 never burns shares it cannot honor. The manager keeps a buffer
    // (and uses bridgeFromCore) to service redemptions.

    function maxWithdraw(address owner) public view override returns (uint256) {
        uint256 byShares = super.maxWithdraw(owner);
        uint256 idle = _idleAssets();
        return byShares < idle ? byShares : idle;
    }

    function maxRedeem(address owner) public view override returns (uint256) {
        uint256 idleInShares = convertToShares(_idleAssets());
        uint256 bal = balanceOf(owner);
        return bal < idleInShares ? bal : idleInShares;
    }

    // --------------------------------------------------------------------- //
    //                      Manager actions (trade-only)                      //
    // --------------------------------------------------------------------- //

    /// @notice Submit the basket's order legs to HyperCore. Manager-only and
    /// restricted to allow-listed assets. Moves no funds out of the vault.
    function submitBasket(Order[] calldata orders) external onlyManager nonReentrant {
        uint256 n = orders.length;
        for (uint256 i; i < n; ++i) {
            Order calldata o = orders[i];
            if (!allowedAsset[o.assetId]) revert AssetNotAllowed(o.assetId);
            _submitOrder(o);
            emit OrderSubmitted(o.assetId, o.isBuy, o.limitPx, o.sz, o.reduceOnly);
        }
        emit BasketSubmitted(n);
    }

    /// @notice Move idle USDC from HyperEVM into the vault's HyperCore account.
    function bridgeToCore(uint256 amount) external onlyManager nonReentrant {
        _bridgeToCore(amount);
        emit BridgedToCore(amount);
    }

    /// @notice Pull USDC from HyperCore back to HyperEVM to service redemptions.
    function bridgeFromCore(uint256 amount) external onlyManager nonReentrant {
        _bridgeFromCore(amount);
        emit BridgedFromCore(amount);
    }

    // --------------------------------------------------------------------- //
    //                              Governance                                //
    // --------------------------------------------------------------------- //

    function setManager(address newManager) external onlyOwner {
        if (newManager == address(0)) revert ZeroAddress();
        manager = newManager;
        emit ManagerUpdated(newManager);
    }

    function setAllowedAsset(uint32 assetId, bool ok) external onlyOwner {
        allowedAsset[assetId] = ok;
        emit AssetAllowed(assetId, ok);
    }

    // --------------------------------------------------------------------- //
    //                  HyperCore integration hooks (virtual)                 //
    // --------------------------------------------------------------------- //

    /// @return equity HyperCore perp account value in underlying units (margin + uPnL).
    function _coreEquityUsd() internal view virtual returns (uint256 equity);

    function _submitOrder(Order calldata order) internal virtual;

    function _bridgeToCore(uint256 amount) internal virtual;

    function _bridgeFromCore(uint256 amount) internal virtual;
}
