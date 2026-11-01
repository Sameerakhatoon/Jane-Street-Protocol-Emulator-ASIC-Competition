#!/usr/bin/env bash
# The I2C target lane in Fast and Standard mode, answering a simulated
# controller: writes, a read with a repeated START and clock stretching
# while the host answers, a NACK for another address and a host-decided
# NACK, each judged on the pins with the timing rules of the speed.
. "$(dirname "$0")/lib.sh"

for speed in fm sm; do
    bash examples/i2c/run.sh target $speed "$OUT/$speed" >"$OUT/$speed.log" 2>&1 \
        || { tail -30 "$OUT/$speed.log" >&2; fail "$speed, see $OUT/$speed.log"; }
done
pass "$(tail -1 "$OUT/fm.log"); $(tail -1 "$OUT/sm.log")"
