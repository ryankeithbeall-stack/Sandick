#!/usr/bin/env bash
# Static analysis over the production contracts with Slither.
#
# Informational by design (--fail-none): it never fails the build, it produces a
# report for review. Tighten to --fail-high once the noise is triaged.
#
# Setup (once):
#   pip install slither-analyzer solc-select
#   solc-select install 0.8.26 && solc-select use 0.8.26
#   npm ci            # OpenZeppelin sources for the @openzeppelin remap
#
# Usage:
#   bash scripts/static_analysis.sh [output-file]
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OUT="${1:-slither-report.txt}"

slither contracts/src \
  --config-file contracts/slither.config.json \
  --fail-none 2>&1 | tee "$OUT"
