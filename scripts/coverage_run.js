// Run the contract test suite under coverage instrumentation and report
// per-file Solidity line coverage. Usage:
//
//   node scripts/coverage_run.js          # report only
//   COVERAGE_MIN=70 node scripts/coverage_run.js   # fail under 70% total
//
// Writes coverage/contracts/{coverage.json,lcov.info}.
const cov = require("./coverage");
const { compile } = require("./compile");

async function main() {
  const collector = cov.enable(); // must precede makeVM() inside the suite
  const { main: runTests } = require("../contracts/test/vault.test.js");
  await runTests();

  const { artifacts, sources } = compile();
  const files = cov.computeCoverage(collector, artifacts, sources);
  const report = cov.formatReport(files);

  console.log("\n" + report.text + "\n");
  cov.writeArtifacts(files, cov.COVERAGE_DIR);
  console.log(`Coverage details written to ${cov.COVERAGE_DIR}`);

  const min = process.env.COVERAGE_MIN ? parseFloat(process.env.COVERAGE_MIN) : null;
  if (min !== null && report.pct < min) {
    console.error(
      `\nContract coverage ${report.pct.toFixed(1)}% is below the required ${min}%.`
    );
    process.exit(1);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
