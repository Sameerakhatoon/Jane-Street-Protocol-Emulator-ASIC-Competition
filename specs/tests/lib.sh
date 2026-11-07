# SPDX-License-Identifier: Apache-2.0
# Shared helpers for the numbered spec tests. Source this from each test; tests run from the repo root.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
TESTS=specs/tests
PY=${PY:-python3}
test_name="$(basename "${0%.*}")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

tsec() { "$PY" -m tools.tsec "$@"; }
pass() { echo "$test_name ... ok"; exit 0; }
fail() { echo "$test_name ... FAIL: $*" >&2; exit 1; }

assert_contains() {
    local hay="$1" needle="$2" what="${3:-output}"
    case "$hay" in
        *"$needle"*) ;;
        *) fail "$what: expected to contain '$needle' but got: $hay" ;;
    esac
}
