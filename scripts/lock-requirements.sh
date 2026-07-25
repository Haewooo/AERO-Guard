#!/bin/bash
# Regenerate the hash-pinned dependency locks.
#
# Runs pip's resolver inside the deployment interpreter (CPython 3.12,
# manylinux) and records a SHA-256 for every wheel, for both x86_64 and
# aarch64, so `pip install --require-hashes` is reproducible regardless of
# which architecture builds the image.
#
# Usage:  ./scripts/lock-requirements.sh      (requires Docker)
set -euo pipefail
cd "$(dirname "$0")/.."

docker run --rm \
  -v "$PWD:/repo:ro" \
  -v "$PWD:/out" \
  -v "$PWD/scripts/_lock-inner.sh:/lock-inner.sh:ro" \
  python:3.12-slim sh /lock-inner.sh

echo
echo "Updated requirements-full-x86_64.lock and requirements-full-aarch64.lock."
echo "Review the diff before committing — a changed hash means a changed artifact."
