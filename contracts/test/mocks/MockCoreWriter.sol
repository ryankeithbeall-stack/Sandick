// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {ICoreWriter} from "../../src/interfaces/ICoreWriter.sol";

/// @dev Recorder for the CoreWriter system contract. Etched at 0x33..33 in the
/// JS harness so the REAL HyperCoreActions encodings (limit order, USD class
/// transfer, spot send) execute and their exact payload bytes can be asserted —
/// covering the production write path the MockBasketVault otherwise bypasses.
contract MockCoreWriter is ICoreWriter {
    bytes public lastData;
    uint256 public callCount;
    bytes[] private _all;

    function sendRawAction(bytes calldata data) external {
        lastData = data;
        _all.push(data);
        callCount++;
    }

    function dataAt(uint256 i) external view returns (bytes memory) {
        return _all[i];
    }
}
