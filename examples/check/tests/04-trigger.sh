#!/usr/bin/env bash
# Trigger mode: two matching writes among seven transactions; each fires
# once, its TRIG record carries the cycle of the STOP that completed the
# sequence, and the trigger-out pin rises a fixed delay after that STOP.
. "$(dirname "$0")/lib.sh"

demo bash examples/trigger/run.sh "$OUT"
grep -q "^PASS trigger records: 2 TRIG records" "$OUT/run.log" || fail "TRIG records"
grep -q "^PASS trigger pin" "$OUT/run.log" || fail "trigger pin"
[ "$(grep -c "pulse 6.00 cycles after the STOP, 4.0[0-9] cycles wide" "$OUT/trigpin.txt")" -eq 2 ] \
    || fail "pulse timing"
pass "$(tail -1 "$OUT/run.log")"
