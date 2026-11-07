#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build every harness top that has RTL available. Usage: ./build.sh [clean]
set -euo pipefail

cd "$(dirname "$0")"
[ "${1:-}" = clean ] && make clean >/dev/null
make -s TOP=echo
for top in uart chip; do
    if make -s TOP=$top >/dev/null 2>&1; then
        echo "built build/$top/tse_sim"
    else
        echo "skipped TOP=$top (RTL missing or build failed, see build/$top/verilator.log)"
    fi
done
