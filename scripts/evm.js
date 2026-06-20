// Minimal pure-JS EVM harness (no native deps) for executing the compiled
// contracts. Uses @ethereumjs/vm message calls + ethers for ABI coding.
const { createVM } = require("@ethereumjs/vm");
const { Common, Hardfork, Mainnet } = require("@ethereumjs/common");
const { createAddressFromString, Account, hexToBytes, bytesToHex } = require("@ethereumjs/util");
const { ethers } = require("ethers");

const GAS = 30_000_000n;

function addr(hexByte) {
  return createAddressFromString("0x" + hexByte.toString(16).padStart(2, "0").repeat(20));
}

// Deterministic test accounts (addresses 0x01..*20, ..0x05..*20).
const ACCOUNTS = {
  deployer: addr(0x11),
  manager: addr(0x22),
  alice: addr(0x33),
  bob: addr(0x44),
  owner: addr(0x55),
};

async function makeVM() {
  const common = new Common({ chain: Mainnet, hardfork: Hardfork.Shanghai });
  const vm = await createVM({ common });
  // Fund accounts so any value/gas accounting passes.
  for (const a of Object.values(ACCOUNTS)) {
    await vm.stateManager.putAccount(a, new Account(0n, 10n ** 24n));
  }
  return vm;
}

async function deploy(vm, art, args, from = ACCOUNTS.deployer) {
  const iface = new ethers.Interface(art.abi);
  const encodedArgs = args.length
    ? iface.encodeDeploy(args).slice(2)
    : "";
  const data = hexToBytes(art.bytecode + encodedArgs);
  const res = await vm.evm.runCall({ caller: from, to: undefined, data, gasLimit: GAS });
  if (res.execResult.exceptionError) {
    throw new Error("deploy reverted: " + JSON.stringify(res.execResult.exceptionError));
  }
  const address = res.createdAddress;
  return new Contract(vm, address, iface);
}

class Contract {
  constructor(vm, address, iface) {
    this.vm = vm;
    this.address = address;
    this.iface = iface;
  }

  // Execute a function (state-changing). Throws on revert (decoding the reason
  // when present). Returns decoded outputs.
  async send(from, fn, args = []) {
    const data = hexToBytes(this.iface.encodeFunctionData(fn, args));
    const res = await this.vm.evm.runCall({
      caller: from,
      to: this.address,
      data,
      gasLimit: GAS,
    });
    const out = res.execResult;
    if (out.exceptionError) {
      let reason = "";
      try {
        reason = this.iface.parseError(bytesToHex(out.returnValue))?.name || "";
      } catch {}
      const e = new Error(`revert ${fn} ${reason}`.trim());
      e.reverted = true;
      throw e;
    }
    const decoded = this.iface.decodeFunctionResult(fn, bytesToHex(out.returnValue));
    return decoded.length === 1 ? decoded[0] : decoded;
  }

  // Read-only call (same engine; we just don't care about state effects).
  async call(fn, args = []) {
    return this.send(ACCOUNTS.deployer, fn, args);
  }
}

module.exports = { makeVM, deploy, ACCOUNTS };
