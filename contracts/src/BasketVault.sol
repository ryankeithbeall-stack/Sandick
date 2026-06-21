// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {BasketVaultBase} from "./BasketVaultBase.sol";
import {HyperCoreActions} from "./lib/HyperCoreActions.sol";
import {IHyperCoreReader} from "./interfaces/IHyperCoreReader.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

/// @title BasketVault
/// @notice Production vault wired to HyperCore via CoreWriter (write path) and a
/// read precompile reader (NAV). The contract itself is the HyperCore trading
/// account — it acts only on its own behalf, which matches CoreWriter semantics.
///
/// One deployable basket vault on the platform. Its name/symbol and basket are
/// supplied at construction (typically by {VaultFactory}); the flagship SANDICK
/// vault is just one instance of this contract.
///
/// Status: the CoreWriter encodings (orders, USD class transfer, spot send) are
/// confirmed against hyper-evm-lib + the official docs. The NAV reader and the
/// USDC system-address / decimal-scaling immutables are UNVERIFIED inputs that
/// must be confirmed on testnet. This contract is UNAUDITED — do not deploy with
/// real funds before an audit and a full testnet sign-off.
contract BasketVault is BasketVaultBase {
    using SafeERC20 for IERC20;
    using HyperCoreActions for *;

    /// @notice NAV reader over HyperCore precompiles.
    IHyperCoreReader public immutable reader;

    /// @notice System address used to bridge USDC EVM<->Core (0x20..<tokenIndex>).
    address public immutable usdcSystemAddress;

    /// @notice HyperCore spot token index for USDC (used by spot-send).
    uint64 public immutable usdcCoreTokenIndex;

    /// @notice Multiplier converting EVM USDC (6dp) amounts to HyperCore integer
    /// units. Set from live spotMeta (evmExtraWeiDecimals). VERIFY before use.
    uint256 public immutable coreScale;

    /// @notice Time-in-force for basket orders (1 ALO, 2 GTC, 3 IOC).
    uint8 public immutable tif;

    constructor(
        IERC20 asset_,
        string memory name_,
        string memory symbol_,
        address manager_,
        address owner_,
        IHyperCoreReader reader_,
        address usdcSystemAddress_,
        uint64 usdcCoreTokenIndex_,
        uint256 coreScale_,
        uint8 tif_,
        address protocolAdmin_,
        address protocolTreasury_,
        uint16 protocolFeeBps_
    )
        BasketVaultBase(
            asset_,
            name_,
            symbol_,
            manager_,
            owner_,
            protocolAdmin_,
            protocolTreasury_,
            protocolFeeBps_
        )
    {
        require(address(reader_) != address(0) && usdcSystemAddress_ != address(0), "zero addr");
        require(coreScale_ > 0, "scale");
        require(tif_ >= 1 && tif_ <= 3, "tif");
        reader = reader_;
        usdcSystemAddress = usdcSystemAddress_;
        usdcCoreTokenIndex = usdcCoreTokenIndex_;
        coreScale = coreScale_;
        tif = tif_;
    }

    function _coreAmount(uint256 evmAmount) internal view returns (uint64) {
        uint256 v = evmAmount * coreScale;
        require(v <= type(uint64).max, "overflow");
        return uint64(v);
    }

    // --------------------------- integration hooks --------------------------- //

    function _coreEquityUsd() internal view override returns (uint256) {
        return reader.accountEquityUsd(address(this));
    }

    function _submitOrder(Order calldata order) internal override {
        // Order.limitPx / Order.sz are already in HyperCore 1e8 integer units,
        // produced by the off-chain planner.
        HyperCoreActions.limitOrder(
            order.assetId,
            order.isBuy,
            order.limitPx,
            order.sz,
            order.reduceOnly,
            tif,
            0 // no client order id
        );
    }

    function _bridgeToCore(uint256 amount) internal override {
        // 1. Move the ERC20 to USDC's system address -> credits Core spot.
        IERC20(asset()).safeTransfer(usdcSystemAddress, amount);
        // 2. Spot -> perp so the funds are usable as margin (async).
        HyperCoreActions.usdClassTransfer(_coreAmount(amount), true);
    }

    function _bridgeFromCore(uint256 amount) internal override {
        uint64 coreAmt = _coreAmount(amount);
        // 1. Perp -> spot.
        HyperCoreActions.usdClassTransfer(coreAmt, false);
        // 2. Spot-send to USDC's system address -> credits this contract on EVM.
        HyperCoreActions.spotSend(usdcSystemAddress, usdcCoreTokenIndex, coreAmt);
    }
}
