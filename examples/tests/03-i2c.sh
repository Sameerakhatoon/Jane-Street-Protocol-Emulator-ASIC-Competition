#!/usr/bin/env bash
# The I2C controller demo end to end in Fast and Standard mode: host side,
# all four transactions decoded as expected (two of them end in a NACK the
# host handles), every timing rule of the speed held (tHIGH and the SCL
# period included).
. "$(dirname "$0")/lib.sh"

for speed in fm sm; do
    bash examples/i2c/run.sh controller $speed "$OUT/$speed" >"$OUT/$speed.log" 2>&1 \
        || { tail -30 "$OUT/$speed.log" >&2; fail "$speed, see $OUT/$speed.log"; }
done
pass "$(tail -1 "$OUT/fm.log"); $(tail -1 "$OUT/sm.log")"
