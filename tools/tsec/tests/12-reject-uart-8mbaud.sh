#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# At 8 Mbaud no strategy meets the obligations: exit 2, diagnostics, and no image (rule R1).
. "$(dirname "$0")/lib.sh"
mkdir -p "$TMP/out"
echo stale > "$TMP/out/uart.img"
golden uart-8000000 tsec build specs/uart.tse -D baud=8000000 -o "$TMP/out"
assert_eq 2 "$status" "exit status"
[ ! -e "$TMP/out/uart.img" ] || fail "a stale image survived a rejection"
err="$(cat "$TMP/uart-8000000.err")"
assert_contains "$err" "specs/uart.tse:31:9: error: output placement outside tolerance" "R310 diagnostic"
assert_contains "$err" "[R314]" "re-arm diagnostic"
assert_contains "$err" "no image written (rule R1)" "summary"
pass
