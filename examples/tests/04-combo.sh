#!/usr/bin/env bash
# Both lanes at once (UART on lane 0, I2C on lane 1). The UART judge and
# the timing cross-check must pass while the other lane runs. Expected
# failure on the I2C side for the same dropped wait as test 03; the day it
# passes, drop that branch.
. "$(dirname "$0")/lib.sh"

bash examples/combo/run.sh "$OUT" >"$OUT/run.log" 2>&1 && pass "$(tail -1 "$OUT/run.log")"
grep -q "^PASS host" "$OUT/run.log" || fail "host side, see $OUT/run.log"
grep -q "^PASS uart" "$OUT/judge-uart.log" || fail "UART judge, see $OUT/judge-uart.log"
grep -q "^PASS timing" "$OUT/timing.log" || fail "UART timing, see $OUT/timing.log"
grep -q "frames differ" "$OUT/judge-i2c.log" && fail "I2C transactions differ"
other="$(grep "^VIOLATION" "$OUT/judge-i2c.log" | grep -v -e " tHIGH: " -e " SCL period (fSCL): " || true)"
[ -z "$other" ] || fail "other I2C violations: $(head -3 <<<"$other")"
pass "UART judged and timed on lane 0; I2C on lane 1 expected failure (tHIGH only)"
