#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# The UART demo end to end: spec, proof, image, loader, lanes, judge.
. "$(dirname "$0")/lib.sh"

bash examples/uart/run.sh "$OUT" >"$OUT/run.log" 2>&1 || { tail -30 "$OUT/run.log" >&2; fail "see $OUT/run.log"; }
last="$(tail -1 "$OUT/run.log")"
case "$last" in
    "PASS e2e-uart:"*) ;;
    *) fail "$last" ;;
esac
pass "$(sed -n "s/^PASS timing: //p" "$OUT/run.log")"
