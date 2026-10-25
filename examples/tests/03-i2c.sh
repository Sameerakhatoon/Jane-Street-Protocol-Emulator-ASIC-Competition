#!/usr/bin/env bash
# The I2C controller demo end to end: host side, both transactions decoded
# as expected, every Fast-mode timing rule held (tHIGH and the SCL period
# included), every byte ACKed.
. "$(dirname "$0")/lib.sh"

bash examples/i2c/run.sh "$OUT" >"$OUT/run.log" 2>&1 || { tail -30 "$OUT/run.log" >&2; fail "see $OUT/run.log"; }
pass "$(tail -1 "$OUT/run.log")"
