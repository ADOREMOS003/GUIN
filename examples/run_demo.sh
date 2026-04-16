#!/usr/bin/env bash
set -euo pipefail

# Example GUIN dry-run invocation.
# Usage:
#   ./examples/run_demo.sh /path/to/bids /path/to/output

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <bids_dir> <output_dir>"
  exit 1
fi

BIDS_DIR="$1"
OUTPUT_DIR="$2"

guin run "skull strip the T1w image for sub-01 using FSL BET" \
  --bids-dir "${BIDS_DIR}" \
  --output-dir "${OUTPUT_DIR}" \
  --dry-run
