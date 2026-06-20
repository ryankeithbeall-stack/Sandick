// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {ERC4626} from "@openzeppelin/contracts/token/ERC20/extensions/ERC4626.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

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
abstract contract SandickVaultBase is ERC4626, Ownable, ReentrancyGuard, Pausable {
    using SafeERC20 for IERC20;
    using Math for uint256;

    /// @notice Strategy key permitted to trade (but never to move funds out).
    address public manager;

    /// @notice Assets (HyperCore asset ids) the manager is allowed to trade.
    mapping(uint32 => bool) public allowedAsset;

    // --- Redemption-liveness backstop ---
    // If the manager key goes dark, queued redemptions could starve (no idle
    // USDC, and bridgeFromCore is manager-only). To guarantee exits, anyone may
    // bridge USDC back from Core once the manager has been inactive for
    // `managerTimeout` seconds — but only up to the outstanding redemption
    // deficit, so the backstop can never pull more than is owed to redeemers.
    /// @notice Timestamp of the manager's last trade/bridge action.
    uint256 public lastManagerAction;
    /// @notice Seconds of manager inactivity after which the redemption backstop
    /// opens. 0 disables the backstop (bridgeFromCore stays manager-only).
    uint64 public managerTimeout;

    // --- Manager order caps (defense in depth) ---
    // Bounds on the raw notional of a single order leg (`limitPx * sz`, in
    // HyperCore integer units) and on the cumulative notional submitted within a
    // rolling epoch. A cap of 0 disables that check; `epochLength` of 0 disables
    // the epoch accounting entirely. Caps never let the manager move funds out —
    // they only narrow how aggressively a compromised/misbehaving manager key can
    // churn the book before the owner can rotate it or pause.
    /// @notice Max raw notional (`limitPx * sz`) for any single order leg (0 = off).
    uint256 public maxOrderNotional;
    /// @notice Max cumulative order notional within one epoch (0 = off).
    uint256 public epochNotionalCap;
    /// @notice Length of the rolling notional epoch in seconds (0 = off).
    uint64 public epochLength;
    /// @notice Start timestamp of the current epoch.
    uint256 public epochStart;
    /// @notice Order notional consumed in the current epoch.
    uint256 public epochNotionalUsed;

    // --- Async redemption queue (ERC-7540-style) ---
    /// @notice Shares escrowed in the vault awaiting fulfillment, per owner.
    mapping(address => uint256) public pendingRedeemShares;
    /// @notice Total escrowed shares awaiting fulfillment.
    uint256 public totalPendingRedeemShares;
    /// @notice USDC settled and owed to an owner, claimable any time.
    mapping(address => uint256) public claimableAssets;
    /// @notice USDC reserved for claims; excluded from NAV and idle liquidity.
    uint256 public reservedAssets;

    // --- Fees (all charged as dilution shares; no USDC ever leaves the vault) ---
    // Management + performance fees mint shares to `feeRecipient` (a treasury),
    // so the "manager can never move funds out" invariant is untouched: the fee
    // recipient is just another share holder who redeems like anyone else. The
    // exit fee is retained *in the vault*, boosting NAV for the holders who stay.
    uint256 internal constant BPS = 10_000;
    uint256 internal constant SECONDS_PER_YEAR = 365 days;
    /// @notice Hard caps on owner-set fees (governance can never exceed these).
    uint16 public constant MAX_MANAGEMENT_FEE_BPS = 500; // 5%/yr
    uint16 public constant MAX_PERFORMANCE_FEE_BPS = 3000; // 30%
    uint16 public constant MAX_EXIT_FEE_BPS = 100; // 1%

    /// @notice Treasury that receives fee shares.
    address public feeRecipient;
    /// @notice Annual management fee (basis points of NAV).
    uint16 public managementFeeBps;
    /// @notice Performance fee (basis points of gains above the high-water mark).
    uint16 public performanceFeeBps;
    /// @notice Exit fee (basis points), retained in the vault on redemption.
    uint16 public exitFeeBps;
    /// @notice High-water mark: highest net price-per-share (1e18) ever reached.
    uint256 public highWaterMark;
    /// @notice Timestamp the management fee was last accrued to.
    uint256 public lastFeeAccrual;

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
    event OrderCapsUpdated(uint256 maxOrderNotional, uint256 epochNotionalCap, uint64 epochLength);
    event ManagerTimeoutUpdated(uint64 timeout);
    event RedemptionBridgeForced(address indexed caller, uint256 amount);
    event FeesAccrued(uint256 managementAssets, uint256 performanceAssets, uint256 sharesMinted);
    event FeeConfigUpdated(address recipient, uint16 managementBps, uint16 performanceBps, uint16 exitBps);

    error NotManager();
    error ZeroAddress();
    error AssetNotAllowed(uint32 assetId);
    error ZeroAmount();
    error ExceedsPending();
    error InsufficientIdleLiquidity();
    error NothingClaimable();
    error OrderNotionalExceeded(uint32 assetId, uint256 notional, uint256 cap);
    error EpochNotionalExceeded(uint256 used, uint256 cap);
    error ManagerStillActive();
    error ExceedsRedemptionDeficit(uint256 requested, uint256 deficit);
    error FeeTooHigh();

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
        lastManagerAction = block.timestamp;
        managerTimeout = 7 days; // backstop opens after a week of manager silence

        feeRecipient = owner_; // treasury defaults to the owner; change via setFeeConfig
        managementFeeBps = 200; // 2%/yr
        performanceFeeBps = 1000; // 10% over high-water mark
        exitFeeBps = 10; // 0.1%, retained in the vault
        lastFeeAccrual = block.timestamp;
        emit ManagerUpdated(manager_);
    }

    /// @dev Record manager liveness; resets the redemption-backstop countdown.
    function _touchManager() internal {
        lastManagerAction = block.timestamp;
    }

    // --------------------------------------------------------------------- //
    //                                  NAV                                   //
    // --------------------------------------------------------------------- //

    /// @notice Vault NAV = idle USDC on HyperEVM + equity on HyperCore (perp margin
    /// + unrealized PnL) + any USDC parked in the Core spot account mid-bridge,
    /// denominated in the underlying asset's units. Excludes assets already
    /// reserved for queued redemptions (those belong to claimers).
    function totalAssets() public view override returns (uint256) {
        return _idleAssets() + _coreEquityUsd() + _coreSpotUsd();
    }

    /// @dev Inflation/donation-attack mitigation via virtual shares.
    function _decimalsOffset() internal pure override returns (uint8) {
        return 6;
    }

    /// @dev Unreserved USDC held on HyperEVM (claim-reserved funds excluded).
    function _idleAssets() internal view returns (uint256) {
        return IERC20(asset()).balanceOf(address(this)) - reservedAssets;
    }

    /// @dev Deposits/mints are pausable; exits (withdraw/redeem/claim) never are,
    /// so a pause can never trap depositor funds.
    function _deposit(address caller, address receiver, uint256 assets, uint256 shares)
        internal
        override
        whenNotPaused
    {
        super._deposit(caller, receiver, assets, shares);
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
        _accrueFees();
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
        _accrueFees();
        // Price at the fee-adjusted NAV, then apply the exit fee (retained in the
        // vault) — consistent with the sync `redeem` path.
        uint256 gross = convertToAssets(shares);
        uint256 assets = gross - _feeOnTotal(gross, exitFeeBps);
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

    /// @notice Submit the basket's order legs to HyperCore. Manager-only,
    /// restricted to allow-listed assets, bounded by the per-order/per-epoch
    /// notional caps, and disabled while paused. Moves no funds out of the vault.
    function submitBasket(Order[] calldata orders) external onlyManager nonReentrant whenNotPaused {
        // Roll the epoch window forward if it has elapsed.
        if (epochLength != 0 && block.timestamp >= epochStart + epochLength) {
            epochStart = block.timestamp;
            epochNotionalUsed = 0;
        }

        uint256 n = orders.length;
        uint256 batchNotional;
        for (uint256 i; i < n; ++i) {
            Order calldata o = orders[i];
            if (!allowedAsset[o.assetId]) revert AssetNotAllowed(o.assetId);

            uint256 ntl = uint256(o.limitPx) * uint256(o.sz);
            if (maxOrderNotional != 0 && ntl > maxOrderNotional) {
                revert OrderNotionalExceeded(o.assetId, ntl, maxOrderNotional);
            }
            batchNotional += ntl;

            _submitOrder(o);
            emit OrderSubmitted(o.assetId, o.isBuy, o.limitPx, o.sz, o.reduceOnly);
        }

        if (epochNotionalCap != 0) {
            epochNotionalUsed += batchNotional;
            if (epochNotionalUsed > epochNotionalCap) {
                revert EpochNotionalExceeded(epochNotionalUsed, epochNotionalCap);
            }
        }
        _touchManager();
        emit BasketSubmitted(n);
    }

    /// @notice Move idle USDC from HyperEVM into the vault's HyperCore account.
    function bridgeToCore(uint256 amount) external onlyManager nonReentrant whenNotPaused {
        _bridgeToCore(amount);
        _touchManager();
        emit BridgedToCore(amount);
    }

    /// @notice Pull USDC from HyperCore back to HyperEVM to service redemptions.
    function bridgeFromCore(uint256 amount) external onlyManager nonReentrant {
        _bridgeFromCore(amount);
        _touchManager();
        emit BridgedFromCore(amount);
    }

    // --------------------------------------------------------------------- //
    //                   Redemption-liveness backstop                        //
    // --------------------------------------------------------------------- //

    /// @notice USDC owed to queued redemptions beyond what idle liquidity covers.
    function redemptionDeficit() public view returns (uint256) {
        uint256 owed = convertToAssets(totalPendingRedeemShares);
        uint256 idle = _idleAssets();
        return owed > idle ? owed - idle : 0;
    }

    /// @notice True when the backstop is enabled and the manager has been silent
    /// for at least `managerTimeout` seconds.
    function managerIsDark() public view returns (bool) {
        return managerTimeout != 0 && block.timestamp > lastManagerAction + managerTimeout;
    }

    /// @notice Permissionless redemption rescue: if the manager has gone dark,
    /// anyone may bridge USDC from Core back to HyperEVM — but only up to the
    /// outstanding redemption deficit. It never moves funds out of the vault
    /// (the USDC lands in the vault's own idle balance for `fulfillRedeem` /
    /// `claim`), never touches Core positions beyond the owed amount, and does
    /// NOT count as manager activity. This is the liveness guarantee: a dark
    /// manager can delay exits but never trap them.
    function bridgeFromCoreForRedemptions(uint256 amount) external nonReentrant {
        if (!managerIsDark()) revert ManagerStillActive();
        if (amount == 0) revert ZeroAmount();
        uint256 deficit = redemptionDeficit();
        if (amount > deficit) revert ExceedsRedemptionDeficit(amount, deficit);
        _bridgeFromCore(amount);
        emit RedemptionBridgeForced(msg.sender, amount);
        emit BridgedFromCore(amount);
    }

    // --------------------------------------------------------------------- //
    //                                 Fees                                   //
    // --------------------------------------------------------------------- //

    /// @notice Set the fee recipient and rates (all bounded by the MAX_* caps).
    /// Accrues at the old rates first so a rate change is never retroactive.
    function setFeeConfig(
        address recipient,
        uint16 managementBps,
        uint16 performanceBps,
        uint16 exitBps
    ) external onlyOwner {
        if (recipient == address(0)) revert ZeroAddress();
        if (
            managementBps > MAX_MANAGEMENT_FEE_BPS
                || performanceBps > MAX_PERFORMANCE_FEE_BPS
                || exitBps > MAX_EXIT_FEE_BPS
        ) revert FeeTooHigh();
        _accrueFees();
        feeRecipient = recipient;
        managementFeeBps = managementBps;
        performanceFeeBps = performanceBps;
        exitFeeBps = exitBps;
        emit FeeConfigUpdated(recipient, managementBps, performanceBps, exitBps);
    }

    /// @notice Net price-per-share (1e18), i.e. NAV / supply.
    function pricePerShare() public view returns (uint256) {
        uint256 supply = totalSupply();
        return supply == 0 ? 0 : totalAssets().mulDiv(1e18, supply);
    }

    /// @notice Poke fee accrual (permissionless; anyone can keep the books current).
    function accrueFees() external {
        _accrueFees();
    }

    /// @dev Accrue management + performance fees as dilution shares to the
    /// treasury. Management fee streams on NAV pro-rata to elapsed time;
    /// performance fee takes a cut of any gain in price-per-share above the
    /// high-water mark. Minting shares keeps NAV constant and dilutes existing
    /// holders by exactly the fee value — no USDC moves. Called before every
    /// deposit/withdraw/redeem and queue action so share price is fee-correct.
    function _accrueFees() internal {
        uint256 ts = block.timestamp;
        uint256 elapsed = ts - lastFeeAccrual;
        lastFeeAccrual = ts;

        uint256 supply = totalSupply();
        uint256 nav = totalAssets();
        if (supply == 0 || nav == 0) return;

        uint256 pps = nav.mulDiv(1e18, supply);
        if (highWaterMark == 0) {
            highWaterMark = pps; // establish the baseline (still charges mgmt below)
        }

        uint256 mgmtAssets =
            elapsed == 0 ? 0 : (nav * elapsed).mulDiv(managementFeeBps, BPS * SECONDS_PER_YEAR);

        uint256 perfAssets = 0;
        if (pps > highWaterMark) {
            uint256 gain = (pps - highWaterMark).mulDiv(supply, 1e18);
            perfAssets = gain.mulDiv(performanceFeeBps, BPS);
        }

        uint256 feeAssets = mgmtAssets + perfAssets;
        uint256 minted = 0;
        if (feeAssets > 0 && feeAssets < nav && feeRecipient != address(0)) {
            // shares whose value equals feeAssets at the post-mint price
            minted = feeAssets.mulDiv(supply, nav - feeAssets);
            if (minted > 0) _mint(feeRecipient, minted);
        }

        // The realized (post-fee) price-per-share is the new high-water mark.
        uint256 newPps = nav.mulDiv(1e18, totalSupply());
        if (newPps > highWaterMark) highWaterMark = newPps;
        emit FeesAccrued(mgmtAssets, perfAssets, minted);
    }

    // Exit fee, applied on the way out (OZ ERC4626Fees-style). The fee stays in
    // the vault, so redeemers pay a small premium that accrues to the holders
    // who remain — and it discourages churn / gaming the redemption queue.
    function _feeOnRaw(uint256 assets, uint256 feeBps) internal pure returns (uint256) {
        return assets.mulDiv(feeBps, BPS, Math.Rounding.Ceil);
    }

    function _feeOnTotal(uint256 assets, uint256 feeBps) internal pure returns (uint256) {
        return assets.mulDiv(feeBps, feeBps + BPS, Math.Rounding.Ceil);
    }

    /// @inheritdoc ERC4626
    function previewWithdraw(uint256 assets) public view override returns (uint256) {
        return super.previewWithdraw(assets + _feeOnRaw(assets, exitFeeBps));
    }

    /// @inheritdoc ERC4626
    function previewRedeem(uint256 shares) public view override returns (uint256) {
        uint256 assets = super.previewRedeem(shares);
        return assets - _feeOnTotal(assets, exitFeeBps);
    }

    // Accrue management/performance fees before any value-changing user action so
    // shares are always priced at the fee-adjusted NAV.
    function deposit(uint256 assets, address receiver) public override returns (uint256) {
        _accrueFees();
        return super.deposit(assets, receiver);
    }

    function mint(uint256 shares, address receiver) public override returns (uint256) {
        _accrueFees();
        return super.mint(shares, receiver);
    }

    function withdraw(uint256 assets, address receiver, address owner)
        public
        override
        returns (uint256)
    {
        _accrueFees();
        return super.withdraw(assets, receiver, owner);
    }

    function redeem(uint256 shares, address receiver, address owner)
        public
        override
        returns (uint256)
    {
        _accrueFees();
        return super.redeem(shares, receiver, owner);
    }

    // --------------------------------------------------------------------- //
    //                              Governance                                //
    // --------------------------------------------------------------------- //

    function setManager(address newManager) external onlyOwner {
        if (newManager == address(0)) revert ZeroAddress();
        manager = newManager;
        lastManagerAction = block.timestamp; // give the new manager a full window
        emit ManagerUpdated(newManager);
    }

    /// @notice Set the manager-inactivity window before the redemption backstop
    /// opens (0 disables it). Owner-only.
    function setManagerTimeout(uint64 timeout) external onlyOwner {
        managerTimeout = timeout;
        emit ManagerTimeoutUpdated(timeout);
    }

    function setAllowedAsset(uint32 assetId, bool ok) external onlyOwner {
        allowedAsset[assetId] = ok;
        emit AssetAllowed(assetId, ok);
    }

    /// @notice Configure the manager order-notional caps (all in HyperCore integer
    /// units; 0 disables the corresponding check). Setting `epochLength` resets the
    /// running epoch so a fresh window starts immediately.
    function setOrderCaps(uint256 maxOrderNotional_, uint256 epochNotionalCap_, uint64 epochLength_)
        external
        onlyOwner
    {
        maxOrderNotional = maxOrderNotional_;
        epochNotionalCap = epochNotionalCap_;
        epochLength = epochLength_;
        epochStart = block.timestamp;
        epochNotionalUsed = 0;
        emit OrderCapsUpdated(maxOrderNotional_, epochNotionalCap_, epochLength_);
    }

    /// @notice Pause deposits/mints and manager trading (submitBasket, bridgeToCore).
    /// Exits — withdraw, redeem, the async queue, claim, and bridgeFromCore — stay
    /// open, so a pause can shrink risk without ever trapping depositor funds.
    function pause() external onlyOwner {
        _pause();
    }

    function unpause() external onlyOwner {
        _unpause();
    }

    // --------------------------------------------------------------------- //
    //                  HyperCore integration hooks (virtual)                 //
    // --------------------------------------------------------------------- //

    /// @return equity HyperCore perp account value in underlying units (margin + uPnL).
    function _coreEquityUsd() internal view virtual returns (uint256 equity);

    /// @notice USDC sitting in the vault's HyperCore *spot* account (e.g. parked
    /// mid-bridge between EVM and the perp margin account), in underlying units.
    /// @dev Defaults to 0 — overridden by deployments that wire the spot-balance
    /// read precompile so in-flight USDC is never dropped from NAV. Counting it
    /// keeps share price continuous across the multi-block bridge.
    function _coreSpotUsd() internal view virtual returns (uint256) {
        return 0;
    }

    function _submitOrder(Order calldata order) internal virtual;

    function _bridgeToCore(uint256 amount) internal virtual;

    function _bridgeFromCore(uint256 amount) internal virtual;
}
