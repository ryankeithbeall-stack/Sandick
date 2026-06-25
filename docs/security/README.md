# Security tooling

## Static analysis (Slither)

Aperture runs [Slither](https://github.com/crytic/slither) over the production
contracts (`contracts/src`) as a static-analysis baseline. It is wired into CI as
an **informational** job (`static-analysis` in `.github/workflows/ci.yml`,
`--fail-none`) that uploads a `slither-report` artifact — it does not block the
required test jobs yet. Tighten `scripts/static_analysis.sh` to `--fail-high`
once the findings are triaged.

Run it locally:

```bash
pip install slither-analyzer solc-select
solc-select install 0.8.26 && solc-select use 0.8.26
npm ci                                  # OpenZeppelin sources for the remap
bash scripts/static_analysis.sh         # writes slither-report.txt
```

Config: `contracts/slither.config.json` (excludes `node_modules` + `contracts/test`,
remaps `@openzeppelin/`).

### Known-benign findings

The current baseline is dominated by two low-severity classes that are expected
for this design and have been reviewed:

- **`block.timestamp` comparisons** — the vault is intentionally time-based
  (management/platform fee accrual, the rolling order-notional epoch, and the
  `managerTimeout` redemption backstop). Second-level timestamp precision is not
  gameable for these uses.
- **unindexed event address parameters** (`FeeConfigUpdated`,
  `ProtocolFeeConfigUpdated`) — cosmetic; the values are recoverable from the
  call data.

No high/critical findings are outstanding. Re-review the report before any
real-funds deployment (see `../../GO-LIVE.md` and the launch sign-off).

## Coverage

Contract line coverage is gated in CI on both a total floor and a **per-file**
floor (`COVERAGE_MIN` / `COVERAGE_MIN_PER_FILE` in the `contracts` job) so a
high total can't hide an untested production file. The production CoreWriter
encoding path is exercised by etching a recorder at the CoreWriter address — see
`contracts/test/mocks/MockCoreWriter.sol`.
