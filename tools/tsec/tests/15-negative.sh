#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Every file in neg/ fails with exactly the diagnostics in its .expect file.
# E codes run `check` (exit 1), R codes run `build` (exit 2); warnings exit 0
# (W220 from `check`, W260 from `build`).
. "$(dirname "$0")/lib.sh"
count=0
for f in $TESTS/neg/*.tse; do
    b="$(basename "$f" .tse)"
    case "$b" in
        R*) cmd=build; want=2 ;;
        W26*) cmd=build; want=0 ;;
        W*) cmd=check; want=0 ;;
        *) cmd=check; want=1 ;;
    esac
    st=0
    tsec $cmd "$f" > /dev/null 2> "$TMP/err" || st=$?
    if [ -n "${TSEC_BLESS:-}" ]; then
        cp "$TMP/err" "$TESTS/neg/$b.expect"
    fi
    assert_eq "$want" "$st" "$b exit status"
    diff -u "$TESTS/neg/$b.expect" "$TMP/err" > "$TMP/diff" || fail "$b: $(head -20 "$TMP/diff")"
    count=$((count + 1))
done
[ "$count" -ge 50 ] || fail "only $count negative tests"
pass
