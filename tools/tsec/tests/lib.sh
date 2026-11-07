# SPDX-License-Identifier: Apache-2.0
# Shared helpers for the numbered tests. Source this from each test; tests run from the repo root.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"
TESTS=tools/tsec/tests
PY=${PY:-python3}
test_name="$(basename "${0%.*}")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

tsec() { "$PY" -m tools.tsec "$@"; }
pass() { echo "$test_name ... ok"; exit 0; }
fail() { echo "$test_name ... FAIL: $*" >&2; exit 1; }

assert_eq() {
    local want="$1" got="$2" what="${3:-value}"
    [ "$want" = "$got" ] || fail "$what: want='$want' got='$got'"
}

assert_contains() {
    local hay="$1" needle="$2" what="${3:-output}"
    case "$hay" in
        *"$needle"*) ;;
        *) fail "$what: expected to contain '$needle' but got: $hay" ;;
    esac
}

# golden NAME CMD...: stdout of CMD must equal golden/NAME.out; sets $status.
# TSEC_BLESS=1 rewrites the golden file instead (review the diff before committing).
golden() {
    local name="$1"
    shift
    status=0
    "$@" > "$TMP/$name.out" 2> "$TMP/$name.err" || status=$?
    if [ -n "${TSEC_BLESS:-}" ]; then
        cp "$TMP/$name.out" "$TESTS/golden/$name.out"
    fi
    diff -u "$TESTS/golden/$name.out" "$TMP/$name.out" > "$TMP/diff" \
        || fail "golden $name differs: $(head -20 "$TMP/diff")"
}
