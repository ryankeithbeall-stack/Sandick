// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {ERC4626} from "@openzeppelin/contracts/token/ERC20/extensions/ERC4626.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
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
    using SafeERC20 for IERC20;

    /// @notice Strategy key permitted to trade (but never to move funds out).
    address public manager;

    /// @notice Assets (HyperCore asset ids) the manager is allowed to trade.
    mapping(uint32 => bool) public allowedAsset;

    // --- Async redemption queue (ERC-7540-style) ---
    /// @notice Shares escrowed in the vault awaiting fulfillment, per owner.
    mapping(address => uint256) public pendingRedeemShares;
    /// @notice Total escrowed shares awaiting fulfillment.
    uint256 public totalPendingRedeemShares;
    /// @notice USDC settled and owed to an owner, claimable any time.
    mapping(address => uint256) public claimableAssets;
    /// @notice USDC reserved for claims; excluded from NAV and idle liquidity.
    uint256 public reservedAssets;

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
    event RedeemRequested(address indexed owner, uint256 shares);
    event RedeemRequestCancelled(address indexed owner, uint256 shares);
    event RedeemFulfilled(address indexed owner, uint256 shares, uint256 assets);
    event RedeemClaimed(address indexed owner, uint256 assets);

    error NotManager();
    error ZeroAddress();
    error AssetNotAllowed(uint32 assetId);
    error ZeroAmount();
    error ExceedsPending();
    error InsufficientIdleLiquidity();
    error NothingClaimable();

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
    /// unrealized PnL), denominated in the underlying asset's units. Excludes
    /// assets already reserved for queued redemptions (those belong to claimers).
    function totalAssets() public view override returns (uint256) {
        return _idleAssets() + _coreEquityUsd();
    }

    /// @dev Inflation/donation-attack mitigation via virtual shares.
    function _decimalsOffset() internal pure override returns (uint8) {
        return 6;
    }

    /// @dev Unreserved USDC held on HyperEVM (claim-reserved funds excluded).
    function _idleAssets() internal view returns (uint256) {
        return IERC20(asset()).balanceOf(address(this)) - reservedAssets;
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
    //                     Async redemption queue (7540-ish)                  //
    // --------------------------------------------------------------------- //
    // For redemptions larger than idle liquidity. Shares are escrowed, then
    // priced and settled at FULFILLMENT time (so the redeemer bears market moves
    // until funds are actually available, not the remaining holders). The
    // manager unwinds positions and bridges funds over later blocks; once idle
    // liquidity exists, fulfillment is PERMISSIONLESS so the manager cannot
    // block a depositor's exit.

    /// @notice Escrow `shares` for asynchronous redemption.
    function requestRedeem(uint256 shares) external nonReentrant {
        if (shares == 0) revert ZeroAmount();
        _transfer(msg.sender, address(this), shares); // reverts if insufficient
        pendingRedeemShares[msg.sender] += shares;
        totalPendingRedeemShares += shares;
        emit RedeemRequested(msg.sender, shares);
    }

    /// @notice Cancel a pending request and get the escrowed shares back.
    function cancelRedeemRequest(uint256 shares) external nonReentrant {
        if (shares == 0) revert ZeroAmount();
        if (pendingRedeemShares[msg.sender] < shares) revert ExceedsPending();
        pendingRedeemShares[msg.sender] -= shares;
        totalPendingRedeemShares -= shares;
        _transfer(address(this), msg.sender, shares);
        emit RedeemRequestCancelled(msg.sender, shares);
    }

    /// @notice Settle `shares` of `owner`'s request at the CURRENT share price,
    /// reserving the USDC for claim. Permissionless; reverts without idle funds.
    function fulfillRedeem(address owner, uint256 shares) public nonReentrant {
        if (shares == 0) revert ZeroAmount();
        if (pendingRedeemShares[owner] < shares) revert ExceedsPending();
        uint256 assets = convertToAssets(shares); // price before burning
        if (_idleAssets() < assets) revert InsufficientIdleLiquidity();

        pendingRedeemShares[owner] -= shares;
        totalPendingRedeemShares -= shares;
        _burn(address(this), shares);
        reservedAssets += assets;
        claimableAssets[owner] += assets;
        emit RedeemFulfilled(owner, shares, assets);
    }

    /// @notice Withdraw assets settled by a prior fulfillment.
    function claim() external nonReentrant {
        uint256 amount = claimableAssets[msg.sender];
        if (amount == 0) revert NothingClaimable();
        claimableAssets[msg.sender] = 0;
        reservedAssets -= amount;
        IERC20(asset()).safeTransfer(msg.sender, amount);
        emit RedeemClaimed(msg.sender, amount);
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
