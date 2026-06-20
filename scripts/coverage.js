// Lightweight Solidity coverage for the in-process @ethereumjs/vm harness.
//
// Hardhat's solidity-coverage can't plug into this repo's custom VM runner, so
// we measure coverage directly: while the tests execute, the EVM emits a `step`
// event per opcode (see scripts/evm.js). We record the executed program
// counters per contract, then map each PC back to a source line using solc's
// deployed-bytecode source map. The result is instruction-derived *line*
// coverage for our own contracts (third-party imports are ignored).
//
// This is an approximation of statement coverage — an executed instruction
// marks its source line covered — but it is honest and directional, and needs
// no extra toolchain.
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");

// ---- collector (driven live by the VM via a global handle) --------------

class Collector {
  constructor() {
    this.executed = new Map(); // "scope:address" -> Set<pc>
    this.names = new Map(); // "scope:address" -> contract name
    this._scope = 0;
  }

  // Each VM instance gets its own scope: addresses are deterministic per VM, so
  // the same address is reused across the suite's many fresh VMs for different
  // contracts. Namespacing by VM keeps those deployments from colliding.
  newScope() {
    return ++this._scope;
  }

  _key(scope, addressHex) {
    return `${scope}:${addressHex.toLowerCase()}`;
  }

  register(scope, addressHex, name) {
    this.names.set(this._key(scope, addressHex), name);
  }

  record(scope, addressHex, pc) {
    const key = this._key(scope, addressHex);
    let set = this.executed.get(key);
    if (!set) {
      set = new Set();
      this.executed.set(key, set);
    }
    set.add(pc);
  }

  // Union of executed PCs across every deployed instance of a contract name.
  executedPcsFor(name) {
    const out = new Set();
    for (const [key, set] of this.executed) {
      if (this.names.get(key) === name) for (const pc of set) out.add(pc);
    }
    return out;
  }
}

function enable() {
  const c = new Collector();
  global.__SANDICK_COVERAGE__ = c;
  return c;
}

function active() {
  return global.__SANDICK_COVERAGE__ || null;
}

// ---- bytecode + source-map decoding ------------------------------------

// Map each instruction's program counter, walking PUSH operands.
function pcList(hexObject) {
  const code = hexObject.startsWith("0x") ? hexObject.slice(2) : hexObject;
  const pcs = [];
  let pc = 0;
  for (let i = 0; i < code.length; i += 2) {
    pcs.push(pc);
    const op = parseInt(code.substr(i, 2), 16);
    let operand = 0;
    if (op >= 0x60 && op <= 0x7f) operand = op - 0x5f; // PUSH1..PUSH32
    pc += 1 + operand;
    i += operand * 2;
  }
  return pcs;
}

// Decompress a solc source map into per-instruction {s,l,f} (offset, length,
// fileId), inheriting fields left blank from the previous entry.
function decodeSourceMap(sourceMap) {
  const entries = [];
  let prev = { s: -1, l: -1, f: -1 };
  for (const piece of sourceMap.split(";")) {
    const parts = piece.split(":");
    const cur = { ...prev };
    if (parts[0] !== undefined && parts[0] !== "") cur.s = parseInt(parts[0], 10);
    if (parts[1] !== undefined && parts[1] !== "") cur.l = parseInt(parts[1], 10);
    if (parts[2] !== undefined && parts[2] !== "") cur.f = parseInt(parts[2], 10);
    entries.push(cur);
    prev = cur;
  }
  return entries;
}

// Byte offset -> 1-based line number for a source file.
function lineIndexer(content) {
  const buf = Buffer.from(content, "utf8");
  const starts = [0];
  for (let i = 0; i < buf.length; i++) if (buf[i] === 0x0a) starts.push(i + 1);
  return (offset) => {
    // binary search: last line start <= offset
    let lo = 0,
      hi = starts.length - 1,
      ans = 0;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (starts[mid] <= offset) {
        ans = mid;
        lo = mid + 1;
      } else hi = mid - 1;
    }
    return ans + 1;
  };
}

// ---- report -------------------------------------------------------------

function isProductionFile(p) {
  // Our deployed contracts, excluding test mocks and pure interfaces.
  return (
    p.startsWith("contracts/src/") && !p.startsWith("contracts/src/interfaces/")
  );
}

// Build per-file {coverable:Set<line>, covered:Set<line>} from the collector
// and the compiler output.
function computeCoverage(collector, artifacts, sources) {
  const files = new Map(); // fileId -> { path, toLine, coverable:Set, covered:Set }
  const fileFor = (f) => {
    if (files.has(f)) return files.get(f);
    const src = sources[f];
    if (!src || !src.content || !isProductionFile(src.path)) {
      files.set(f, null);
      return null;
    }
    const rec = {
      path: src.path,
      toLine: lineIndexer(src.content),
      coverable: new Set(),
      covered: new Set(),
    };
    files.set(f, rec);
    return rec;
  };

  for (const art of Object.values(artifacts)) {
    if (!art.deployedBytecode || art.deployedBytecode === "0x") continue;
    const pcs = pcList(art.deployedBytecode);
    const smap = decodeSourceMap(art.deployedSourceMap);
    const executed = collector.executedPcsFor(art.name);
    for (let i = 0; i < pcs.length && i < smap.length; i++) {
      const { s, f } = smap[i];
      if (f < 0 || s < 0) continue;
      const rec = fileFor(f);
      if (!rec) continue;
      const line = rec.toLine(s);
      rec.coverable.add(line);
      if (executed.has(pcs[i])) rec.covered.add(line);
    }
  }

  const result = [];
  for (const rec of files.values()) {
    if (!rec || rec.coverable.size === 0) continue;
    result.push({
      path: rec.path,
      total: rec.coverable.size,
      hit: rec.covered.size,
      coverable: [...rec.coverable].sort((a, b) => a - b),
      covered: rec.covered,
    });
  }
  result.sort((a, b) => a.path.localeCompare(b.path));
  return result;
}

function formatReport(files) {
  const totalLines = files.reduce((s, f) => s + f.total, 0);
  const totalHit = files.reduce((s, f) => s + f.hit, 0);
  const pct = (h, t) => (t === 0 ? 100 : (100 * h) / t);
  const lines = [];
  lines.push("Solidity line coverage (instruction-derived)");
  lines.push("-".repeat(64));
  lines.push(`  ${"FILE".padEnd(44)}${"LINES".padStart(8)}${"COVER".padStart(8)}`);
  lines.push("-".repeat(64));
  for (const f of files) {
    const label = f.path.replace(/^contracts\//, "");
    lines.push(
      `  ${label.padEnd(44)}${`${f.hit}/${f.total}`.padStart(8)}${pct(f.hit, f.total)
        .toFixed(1)
        .padStart(7)}%`
    );
  }
  lines.push("-".repeat(64));
  lines.push(
    `  ${"TOTAL".padEnd(44)}${`${totalHit}/${totalLines}`.padStart(8)}${pct(totalHit, totalLines)
      .toFixed(1)
      .padStart(7)}%`
  );
  return { text: lines.join("\n"), pct: pct(totalHit, totalLines), totalHit, totalLines };
}

function writeArtifacts(files, dir) {
  fs.mkdirSync(dir, { recursive: true });
  // JSON summary
  const summary = files.map((f) => ({
    file: f.path,
    lines_total: f.total,
    lines_hit: f.hit,
    uncovered: f.coverable.filter((l) => !f.covered.has(l)),
  }));
  fs.writeFileSync(path.join(dir, "coverage.json"), JSON.stringify(summary, null, 2) + "\n");

  // lcov for CI / external tooling
  const lcov = [];
  for (const f of files) {
    lcov.push(`SF:${f.path}`);
    for (const line of f.coverable) lcov.push(`DA:${line},${f.covered.has(line) ? 1 : 0}`);
    lcov.push(`LF:${f.total}`);
    lcov.push(`LH:${f.hit}`);
    lcov.push("end_of_record");
  }
  fs.writeFileSync(path.join(dir, "lcov.info"), lcov.join("\n") + "\n");
}

module.exports = {
  Collector,
  enable,
  active,
  pcList,
  decodeSourceMap,
  lineIndexer,
  computeCoverage,
  formatReport,
  writeArtifacts,
  isProductionFile,
  COVERAGE_DIR: path.join(ROOT, "coverage", "contracts"),
};
