#!/usr/bin/env bash
# 10BASE-T transmit lane (a frame role on the frame engine): three frames staged in the
# data window, sent at 10 Mbit/s and judged by the 10BASE-T judge on the pins.
. "$(dirname "$0")/lib.sh"

bash examples/eth10/run.sh "$OUT" >"$OUT/run.log" 2>&1 \
    || { tail -30 "$OUT/run.log" >&2; fail "see $OUT/run.log"; }
pass "$(tail -1 "$OUT/run.log")"
