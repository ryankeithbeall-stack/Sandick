// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {BasketVault} from "./BasketVault.sol";
import {BasketVaultBase} from "./BasketVaultBase.sol";
import {IHyperCoreReader} from "./interfaces/IHyperCoreReader.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

/// @title VaultFactory
/// @notice The platform: anyone can deploy a {BasketVault} through this factory.
/// Every vault it creates is wired with the platform's protocol fee and treasury,
/// and the factory keeps itself as each vault's `protocolAdmin` — so the platform
/// (and only the platform) can adjust the fee it earns from any hosted vault. The
/// vault `owner` is the creator, who runs their own strategy and operator fees but
/// can never zero out the platform's cut.
///
/// The flagship SANDICK vault is simply the first vault created here.
contract VaultFactory is Ownable {
    /// @notice Immutable HyperCore wiring shared by every vault on a given chain.
    /// Bundled into a struct to keep `createVault`'s signature manageable.
    struct CoreParams {
        IHyperCoreReader reader;
        address usdcSystemAddress;
        uint64 usdcCoreTokenIndex;
        uint256 coreScale;
        uint8 tif;
    }

    /// @notice One record per deployed vault, for enumeration / the platform UI.
    struct VaultRecord {
        address vault;
        address creator;
        address manager;
        address asset;
        string name;
        uint256 createdAt;
    }

    /// @notice Every vault ever created by this factory, in creation order.
    VaultRecord[] public vaults;
    /// @notice True for any address this factory deployed (cheap membership check).
    mapping(address => bool) public isVault;

    /// @notice Treasury that receives the platform fee from every new vault.
    address public protocolTreasury;
    /// @notice Platform fee (bps/yr of NAV) stamped onto every new vault.
    uint16 public protocolFeeBps;
    /// @notice Mirror of {BasketVaultBase-MAX_PROTOCOL_FEE_BPS}; the per-vault
    /// constructor enforces it too, this is just a fail-fast at the factory.
    uint16 public constant MAX_PROTOCOL_FEE_BPS = 200; // 2%/yr

    event VaultCreated(
        address indexed vault,
        address indexed creator,
        address indexed manager,
        address asset,
        string name,
        string symbol
    );
    event DefaultProtocolFeeUpdated(address treasury, uint16 feeBps);

    error FeeTooHigh();
    error ZeroAddress();

    constructor(address owner_, address treasury_, uint16 feeBps_) Ownable(owner_) {
        if (feeBps_ > MAX_PROTOCOL_FEE_BPS) revert FeeTooHigh();
        if (feeBps_ > 0 && treasury_ == address(0)) revert ZeroAddress();
        protocolTreasury = treasury_;
        protocolFeeBps = feeBps_;
        emit DefaultProtocolFeeUpdated(treasury_, feeBps_);
    }

    /// @notice Deploy a new basket vault. The caller becomes the vault `owner`
    /// (operator); the platform retains the protocol fee and stays `protocolAdmin`.
    /// @param asset      ERC-20 the vault custodies (USDC).
    /// @param name       Share-token name (e.g. "SANDICK Vault").
    /// @param symbol     Share-token symbol (e.g. "sSANDICK").
    /// @param manager    Trade-only strategy key for the new vault.
    /// @param core       HyperCore wiring (reader, USDC system addr, scale, tif).
    /// @return vault     The address of the newly deployed vault.
    function createVault(
        IERC20 asset,
        string calldata name,
        string calldata symbol,
        address manager,
        CoreParams calldata core
    ) external returns (address vault) {
        BasketVault v = new BasketVault(
            asset,
            name,
            symbol,
            manager,
            msg.sender, // owner / operator = the creator
            core.reader,
            core.usdcSystemAddress,
            core.usdcCoreTokenIndex,
            core.coreScale,
            core.tif,
            address(this), // protocolAdmin = the platform, not the creator
            protocolTreasury,
            protocolFeeBps
        );
        vault = address(v);

        vaults.push(
            VaultRecord({
                vault: vault,
                creator: msg.sender,
                manager: manager,
                asset: address(asset),
                name: name,
                createdAt: block.timestamp
            })
        );
        isVault[vault] = true;
        emit VaultCreated(vault, msg.sender, manager, address(asset), name, symbol);
    }

    /// @notice Number of vaults this factory has deployed.
    function vaultCount() external view returns (uint256) {
        return vaults.length;
    }

    /// @notice All deployed vault addresses, in creation order.
    function allVaults() external view returns (address[] memory out) {
        out = new address[](vaults.length);
        for (uint256 i; i < vaults.length; ++i) {
            out[i] = vaults[i].vault;
        }
    }

    // --------------------------------------------------------------------- //
    //                         Platform governance                            //
    // --------------------------------------------------------------------- //

    /// @notice Update the platform fee / treasury applied to *future* vaults.
    function setDefaultProtocolFee(address treasury, uint16 feeBps) external onlyOwner {
        if (feeBps > MAX_PROTOCOL_FEE_BPS) revert FeeTooHigh();
        if (feeBps > 0 && treasury == address(0)) revert ZeroAddress();
        protocolTreasury = treasury;
        protocolFeeBps = feeBps;
        emit DefaultProtocolFeeUpdated(treasury, feeBps);
    }

    /// @notice Update the platform fee on an *already-deployed* vault. Works
    /// because the factory is that vault's `protocolAdmin`. Platform-owner only.
    function setVaultProtocolFee(address vault, address treasury, uint16 feeBps) external onlyOwner {
        BasketVaultBase(vault).setProtocolFeeConfig(treasury, feeBps);
    }

    /// @notice Migrate a vault's platform-fee governance to a new admin (e.g. a
    /// new factory or a governance multisig). Platform-owner only.
    function setVaultProtocolAdmin(address vault, address newAdmin) external onlyOwner {
        BasketVaultBase(vault).setProtocolAdmin(newAdmin);
    }
}
