#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Every file in pos/ passes the front end; those whose first line is `// build` also build.
. "$(dirname "$0")/lib.sh"
for f in $TESTS/pos/*.tse; do
    out="$(tsec check "$f" 2>&1)" || fail "$f: $out"
    if head -1 "$f" | grep -q '^// build'; then
        out="$(tsec build "$f" 2>&1)" || fail "build $f: $out"
        assert_contains "$out" "RESULT: all roles accepted" "$f"
    fi
done
pass
