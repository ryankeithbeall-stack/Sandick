// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {ICoreWriter} from "../interfaces/ICoreWriter.sol";

/// @title HyperCoreActions
/// @notice Encodes and sends HyperCore actions through the CoreWriter system
/// contract from a HyperEVM contract.
///
/// Wire format (confirmed against hyperliquid-dev/hyper-evm-lib CoreWriterLib.sol
/// + HLConstants.sol and the official "Interacting with HyperCore" docs):
///   payload = abi.encodePacked(uint8(1) version, uint24(actionId), abi.encode(args))
/// CoreWriter address: 0x3333333333333333333333333333333333333333
///
/// IMPORTANT — CoreWriter is fire-and-forget and ASYNCHRONOUS:
///   * It returns nothing and does NOT revert if the HyperCore action fails
///     (e.g. insufficient margin, invalid asset, account not yet funded).
///   * Order/vault actions are intentionally delayed a few seconds and execute
///     on a later Core block. Never assume an order placed here has filled;
///     verify via read precompiles in a later block or off-chain.
library HyperCoreActions {
    address internal constant CORE_WRITER = 0x3333333333333333333333333333333333333333;

    uint8 internal constant ENCODING_VERSION = 1;

    // Action IDs (uint24), from HLConstants.sol.
    uint24 internal constant ACTION_LIMIT_ORDER = 1;
    uint24 internal constant ACTION_USD_CLASS_TRANSFER = 7;
    uint24 internal constant ACTION_SPOT_SEND = 6;

    // Time-in-force encodings.
    uint8 internal constant TIF_ALO = 1;
    uint8 internal constant TIF_GTC = 2;
    uint8 internal constant TIF_IOC = 3;

    // HyperCore prices and sizes are integers scaled by 1e8.
    uint256 internal constant PX_SZ_SCALE = 1e8;

    function _send(bytes memory data) private {
        ICoreWriter(CORE_WRITER).sendRawAction(data);
    }

    /// @notice Place a limit order on a perp (incl. HIP-3) or spot market.
    /// @param asset HyperCore asset id (HIP-3: 100000 + dexIndex*10000 + metaIndex).
    /// @param isBuy true = bid, false = ask.
    /// @param limitPx price * 1e8. @param sz size * 1e8.
    /// @param reduceOnly true = may only shrink an existing position.
    /// @param tif time-in-force (1 ALO, 2 GTC, 3 IOC). @param cloid 0 = none.
    function limitOrder(
        uint32 asset,
        bool isBuy,
        uint64 limitPx,
        uint64 sz,
        bool reduceOnly,
        uint8 tif,
        uint128 cloid
    ) internal {
        _send(
            abi.encodePacked(
                ENCODING_VERSION,
                ACTION_LIMIT_ORDER,
                abi.encode(asset, isBuy, limitPx, sz, reduceOnly, tif, cloid)
            )
        );
    }

    /// @notice Move USDC between the spot and perp sub-accounts.
    /// @param ntl USDC amount in HyperCore integer units. @param toPerp spot->perp if true.
    function usdClassTransfer(uint64 ntl, bool toPerp) internal {
        _send(
            abi.encodePacked(
                ENCODING_VERSION,
                ACTION_USD_CLASS_TRANSFER,
                abi.encode(ntl, toPerp)
            )
        );
    }

    /// @notice Spot-send a token. Used to bridge Core -> EVM by sending to the
    /// token's system address (0x20..<tokenIndex>).
    function spotSend(address to, uint64 token, uint64 amountWei) internal {
        _send(
            abi.encodePacked(
                ENCODING_VERSION,
                ACTION_SPOT_SEND,
                abi.encode(to, token, amountWei)
            )
        );
    }
}
