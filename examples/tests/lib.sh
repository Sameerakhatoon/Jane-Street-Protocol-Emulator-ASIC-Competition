# Shared helpers for the numbered example tests. Each test runs from the
# repository root and writes under build/e2e/.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
test_name="$(basename "${0%.*}")"
OUT="$ROOT/build/e2e/$test_name"
mkdir -p "$OUT"

pass() { echo "$test_name ... ok${1:+ ($*)}"; exit 0; }
fail() { echo "$test_name ... FAIL: $*" >&2; exit 1; }
skip() { echo "$test_name ... skipped: $*"; exit 77; }
