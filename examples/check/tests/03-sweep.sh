#!/usr/bin/env bash
# Margin sweep against the chip: the START hold goes from 245 cycles down by
# one per step; 241 is the last clean pass, 240 is "possible" (one tick of
# sampling either side of the limit), 239 and below are certain; the judge,
# which has no sampling uncertainty, first flags 239.
. "$(dirname "$0")/lib.sh"

demo bash examples/check/run.sh sweep "$OUT"
grep -q "^last passing offset: 4 t" "$OUT/run.log" || fail "last passing step"
grep -q "^failing edge: tHD;STA, lane 0, .* measured 240 t (4.000 us), margin +0 t" "$OUT/run.log" \
    || fail "failing edge"
[ "$(grep -c "possible  tHD;STA" "$OUT/report.txt")" -eq 1 ] || fail "one possible step"
[ "$(grep -c "VIOLATION  tHD;STA" "$OUT/report.txt")" -eq 4 ] || fail "four certain steps"
grep -q "^VIOLATION .* tHD;STA: measured 3.984 us" "$OUT/judge.log" || fail "judge's first violation"
[ "$(grep -c "^VIOLATION" "$OUT/judge.log")" -eq 4 ] || fail "judge count"
pass "$(tail -1 "$OUT/run.log")"
