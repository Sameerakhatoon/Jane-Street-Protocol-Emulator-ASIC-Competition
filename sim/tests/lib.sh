# Shared test helpers. Source from each test. Each test runs in sim/.
set -euo pipefail

SIM_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SIM_ROOT"

test_name="$(basename "${0%.*}")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass() { echo "$test_name ... ok${1:+ ($*)}"; exit 0; }
fail() { echo "$test_name ... FAIL: $*" >&2; exit 1; }
skip() { echo "$test_name ... skipped: $*"; exit 77; }

# build_top <top>: make sure build/<top>/tse_sim is fresh.
build_top() {
    make -s --no-print-directory TOP="$1" >"$TMP/build-$1.log" 2>&1 || { tail -20 "$TMP/build-$1.log" >&2; fail "build TOP=$1"; }
}

assert_contains() {
    local hay="$1" needle="$2" what="${3:-output}"
    case "$hay" in
        *"$needle"*) ;;
        *) fail "$what: expected to contain '$needle' but got: $hay" ;;
    esac
}

assert_eq() {
    local want="$1" got="$2" what="${3:-value}"
    if [ "$want" != "$got" ]; then
        fail "$what: want='$want' got='$got'"
    fi
}
