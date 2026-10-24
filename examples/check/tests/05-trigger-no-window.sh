#!/usr/bin/env bash
# Planted fault: the same trigger without its stage windows also fires on
# the near misses (0x10 one byte late, STOP one byte late, a repeated
# START with a later STOP). The pin judge, whose ground truth comes from
# the decoded trace, must fail and name them.
. "$(dirname "$0")/lib.sh"

VCD="$OUT/trigger.vcd"
python3 examples/trigger/trigger_demo.py --sim "$SIM --pin-server --vcd $VCD" --out "$OUT" \
    --window 0 >"$OUT/run.log" 2>&1 && fail "the demo accepted extra TRIG records"
grep -q "^FAIL trigger: .*5 TRIG records, 2 matching writes" "$OUT/run.log" || fail "TRIG count"
FROM="$(sed -n "s/^window: \([0-9.]*\) s$/\1/p" "$OUT/run.log")"
python3 examples/trigger/trigpin.py "$VCD" --from "$FROM" --width 4 >"$OUT/trigpin.txt" \
    && fail "the pin judge passed"
n="$(grep -o "pulse after a near miss" "$OUT/trigpin.txt" | wc -l)"
[ "$n" -eq 3 ] || fail "$n near misses named, want 3"
pass "3 near-miss pulses caught"
