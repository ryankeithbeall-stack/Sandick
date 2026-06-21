// Minimal solc-js compile check (no Foundry in this env).
// Compiles the vault + mocks and reports errors/warnings.
const fs = require("fs");
const path = require("path");
const solc = require("solc");

const ROOT = path.resolve(__dirname, "..");

function readDisk(p) {
  return fs.readFileSync(path.join(ROOT, p), "utf8");
}

// Entry sources (keyed by repo-relative path; solc canonicalizes relative imports).
const entries = [
  "contracts/src/BasketVault.sol",
  "contracts/src/VaultFactory.sol",
  "contracts/src/HyperCoreReader.sol",
  "contracts/test/mocks/MockBasketVault.sol",
  "contracts/test/mocks/MockERC20.sol",
  "contracts/test/mocks/MockMarginSummary.sol",
];

const sources = {};
for (const e of entries) sources[e] = { content: readDisk(e) };

function findImport(importPath) {
  try {
    if (importPath.startsWith("@openzeppelin/")) {
      return { contents: readDisk(path.join("node_modules", importPath)) };
    }
    return { contents: readDisk(importPath) };
  } catch (err) {
    return { error: "Not found: " + importPath };
  }
}

const input = {
  language: "Solidity",
  sources,
  settings: {
    optimizer: { enabled: true, runs: 200 },
    viaIR: true,
    outputSelection: { "*": { "*": ["abi", "evm.bytecode.object"] } },
  },
};

const out = JSON.parse(
  solc.compile(JSON.stringify(input), { import: findImport })
);

const errors = (out.errors || []).filter((e) => e.severity === "error");
const warnings = (out.errors || []).filter((e) => e.severity === "warning");

for (const w of warnings) console.log("WARNING:", w.formattedMessage.split("\n")[0]);
if (errors.length) {
  console.error("\nCOMPILE FAILED:");
  for (const e of errors) console.error(e.formattedMessage);
  process.exit(1);
}

const contracts = out.contracts || {};
let count = 0;
for (const file of Object.keys(contracts))
  for (const name of Object.keys(contracts[file])) count++;
console.log(`\nOK: compiled ${count} contracts, ${warnings.length} warning(s).`);
