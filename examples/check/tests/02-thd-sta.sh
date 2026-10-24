#!/usr/bin/env bash
# START hold of 188 cycles: the chip flags every START as a certain tHD;STA
# violation measured at 3.133 us against 4.000 us, the judge flags the
# same ones, and nothing else is flagged.
. "$(dirname "$0")/lib.sh"

demo bash examples/check/run.sh thd-sta "$OUT"
line="t=.*  VIOLATION  tHD;STA  measured 3.133 us  min 4.000 us  (lane 0)"
n="$(grep -c "^$line" "$OUT/report.txt" || true)"
[ "$n" -eq 4 ] || fail "$n report lines, want 4 (three STARTs and a repeated START)"
grep -q "^tHD;STA .* 4 .* 188 t .* -52 t .*VIOLATION" "$OUT/report.txt" || fail "summary row"
pass "$(tail -1 "$OUT/run.log")"
