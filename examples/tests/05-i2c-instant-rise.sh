#!/usr/bin/env bash
# The I2C controller on a bus that rises in the same clock SCL is released:
# the wait for the rise after our own release must not first wait for a low
# level the bus never shows again (it used to hang there).
. "$(dirname "$0")/lib.sh"

TR=0 bash examples/i2c/run.sh "$OUT" >"$OUT/run.log" 2>&1 || { tail -30 "$OUT/run.log" >&2; fail "see $OUT/run.log"; }
pass "$(tail -1 "$OUT/run.log")"
