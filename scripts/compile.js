// Reusable solc compile helper. Returns { "File.sol:Name": {abi, bytecode} }.
const fs = require("fs");
const path = require("path");
const solc = require("solc");

const ROOT = path.resolve(__dirname, "..");
const read = (p) => fs.readFileSync(path.join(ROOT, p), "utf8");

const ENTRIES = [
  "contracts/src/BasketVault.sol",
  "contracts/src/VaultFactory.sol",
  "contracts/src/HyperCoreReader.sol",
  "contracts/test/mocks/MockBasketVault.sol",
  "contracts/test/mocks/MockERC20.sol",
  "contracts/test/mocks/MockMarginSummary.sol",
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
      outputSelection: {
        "*": {
          "*": [
            "abi",
            "evm.bytecode.object",
            "evm.deployedBytecode.object",
            "evm.deployedBytecode.sourceMap",
          ],
        },
      },
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
      artifacts[name] = {
        name,
        file,
        abi: c.abi,
        bytecode: "0x" + c.evm.bytecode.object,
        deployedBytecode: "0x" + c.evm.deployedBytecode.object,
        deployedSourceMap: c.evm.deployedBytecode.sourceMap || "",
      };
    }
  }
  // id -> { path, content } for every source solc touched. Content is only
  // loaded for our own contracts (the targets of coverage); third-party
  // imports (e.g. OpenZeppelin) are left without content and ignored.
  const srcById = {};
  for (const [p, meta] of Object.entries(out.sources || {})) {
    let content = null;
    if (p.startsWith("contracts/")) {
      try {
        content = read(p);
      } catch (e) {
        content = null;
      }
    }
    srcById[meta.id] = { path: p, content };
  }
  const warnings = (out.errors || []).filter((e) => e.severity === "warning");
  return { artifacts, warnings, sources: srcById };
}

module.exports = { compile };
