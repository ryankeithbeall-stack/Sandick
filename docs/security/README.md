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

## Linting (solhint)

[solhint](https://github.com/protofire/solhint) lints the production contracts as
an **informational** CI step (`static-analysis` job, `continue-on-error`), mirroring
the Slither posture. Config lives in `.solhint.json` (extends `solhint:recommended`,
pins the compiler to 0.8.26). A few `solhint:recommended` rules are turned off
because they conflict with deliberate, documented design choices:

- **`not-rely-on-time`** — the vault is intentionally time-based (fee accrual, the
  rolling order epoch, the `managerTimeout` backstop); same rationale as the
  Slither `block.timestamp` note above.
- **`gas-indexed-events`** — most event params are intentionally left unindexed
  (the values are recoverable from call data); same as the Slither note.
- **`immutable-vars-naming`** — the codebase uses `lowerCamelCase` immutables.
- Misc gas-style nits (`gas-strict-inequalities`, `gas-struct-packing`,
  `max-states-count`) and `use-natspec` are off as noise.

The baseline is **clean** (0 warnings) with this config, so the job can be tightened
from informational to blocking by dropping `continue-on-error`.

Run it locally:

```bash
npm ci
npm run lint:sol     # solhint 'contracts/src/**/*.sol'
```

## Invariant / fuzz harness

`contracts/test/invariant.test.js` (run via `npm run test:invariant`, also in CI)
drives randomized but **valid** action sequences against `MockBasketVault` on the
same in-process @ethereumjs/vm engine as the unit tests, and asserts the core
safety invariants after every action:

- NAV accounting identity (`totalAssets == idle + coreEquity + coreSpot`),
- solvency (real custody ≥ NAV + reserved claims),
- redemption-queue conservation (escrowed shares ↔ pending ↔ reserved/claimable),
- the trade-only manager can never receive vault funds,
- bridges to/from Core are NAV-neutral, and
- a deposit→redeem round trip can never profit (no rounding extraction).

Randomness is a deterministic mulberry32 PRNG seeded from a committed constant
array, so any failure reproduces exactly from `(seed, step)` (set `FUZZ_DEBUG=1`
to log bounded-arg skips). Fee-on / share-price-monotonicity fuzzing is out of
scope (dilution-fee mints lower price-per-share, so that is not a valid invariant).

## Coverage

Contract line coverage is gated in CI on both a total floor and a **per-file**
floor (`COVERAGE_MIN` / `COVERAGE_MIN_PER_FILE` in the `contracts` job) so a
high total can't hide an untested production file. The production CoreWriter
encoding path is exercised by etching a recorder at the CoreWriter address — see
`contracts/test/mocks/MockCoreWriter.sol`.
