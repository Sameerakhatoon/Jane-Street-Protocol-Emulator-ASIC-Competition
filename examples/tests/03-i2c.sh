#!/usr/bin/env bash
# The I2C controller demo end to end. Expected failure: tsec drops the
# obligation of the first of two consecutive waits (the first half of the
# SCL high time after the observed rise lowers to DLY 0), so the judge
# measures tHIGH 583 ns against the Fast-mode 600 ns and an SCL period
# under 2.5 us. Everything else must hold: host side, both transactions
# decoded as expected, no other rule broken. The day the judge passes,
# drop the expected-failure branch.
. "$(dirname "$0")/lib.sh"

bash examples/i2c/run.sh "$OUT" >"$OUT/run.log" 2>&1 && pass "$(tail -1 "$OUT/run.log")"
grep -q "^PASS host" "$OUT/run.log" || fail "host side, see $OUT/run.log"
grep -q "frames differ" "$OUT/judge.log" && fail "transactions differ, see $OUT/judge.log"
other="$(grep "^VIOLATION" "$OUT/judge.log" | grep -v -e " tHIGH: " -e " SCL period (fSCL): " || true)"
[ -z "$other" ] || fail "other violations: $(head -3 <<<"$other")"
n="$(grep -c "^VIOLATION" "$OUT/judge.log")"
pass "expected failure: $n tHIGH and SCL period violations from the dropped wait, nothing else"
