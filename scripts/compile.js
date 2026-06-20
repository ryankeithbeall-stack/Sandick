// Reusable solc compile helper. Returns { "File.sol:Name": {abi, bytecode} }.
const fs = require("fs");
const path = require("path");
const solc = require("solc");

const ROOT = path.resolve(__dirname, "..");
const read = (p) => fs.readFileSync(path.join(ROOT, p), "utf8");

const ENTRIES = [
  "contracts/src/SandickVault.sol",
  "contracts/src/HyperCoreReader.sol",
  "contracts/test/mocks/MockSandickVault.sol",
  "contracts/test/mocks/MockERC20.sol",
];

function findImport(importPath) {
  try {
    const rel = importPath.startsWith("@openzeppelin/")
      ? path.join("node_modules", importPath)
      : importPath;
    return { contents: read(rel) };
  } catch (e) {
    return { error: "Not found: " + importPath };
  }
}

function compile() {
  const sources = {};
  for (const e of ENTRIES) sources[e] = { content: read(e) };
  const input = {
    language: "Solidity",
    sources,
    settings: {
      optimizer: { enabled: true, runs: 200 },
      evmVersion: "shanghai",
      outputSelection: { "*": { "*": ["abi", "evm.bytecode.object"] } },
    },
  };
  const out = JSON.parse(solc.compile(JSON.stringify(input), { import: findImport }));
  const errors = (out.errors || []).filter((e) => e.severity === "error");
  if (errors.length) {
    for (const e of errors) console.error(e.formattedMessage);
    throw new Error("compile failed");
  }
  const artifacts = {};
  for (const file of Object.keys(out.contracts || {})) {
    for (const name of Object.keys(out.contracts[file])) {
      const c = out.contracts[file][name];
      artifacts[name] = { abi: c.abi, bytecode: "0x" + c.evm.bytecode.object };
    }
  }
  const warnings = (out.errors || []).filter((e) => e.severity === "warning");
  return { artifacts, warnings };
}

module.exports = { compile };
