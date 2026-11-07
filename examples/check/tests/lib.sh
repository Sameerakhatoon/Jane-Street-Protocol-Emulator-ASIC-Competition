# SPDX-License-Identifier: Apache-2.0
# Shared helpers for the numbered Check and Trigger tests. Each test runs
# from the repository root and writes under build/check/tests/.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"
export PYTHONDONTWRITEBYTECODE=1
test_name="$(basename "${0%.*}")"
OUT="$ROOT/build/check/tests/$test_name"
mkdir -p "$OUT"
SIM="$ROOT/sim/build/chip/tse_sim"

pass() { echo "$test_name ... ok${1:+ ($*)}"; exit 0; }
fail() { echo "$test_name ... FAIL: $*" >&2; exit 1; }

# run a demo script, keep its log, fail with the log's tail
demo() {
    local log="$OUT/run.log"
    "$@" >"$log" 2>&1 || { tail -30 "$log" >&2; fail "$* (log $log)"; }
}
make -s --no-print-directory -C sim TOP=chip >"$OUT/sim-build.log" 2>&1 || fail "sim build, see $OUT/sim-build.log"
