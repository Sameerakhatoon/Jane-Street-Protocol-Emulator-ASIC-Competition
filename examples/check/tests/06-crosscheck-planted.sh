#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Planted fault: one SCL fall record of the clean run moved one tick later
# must make the crosscheck fail (tLOW, tHIGH and tHD;DAT then differ from
# the judge by a whole tick). Reuses the trace of test 01.
. "$(dirname "$0")/lib.sh"

SRC="$ROOT/build/check/tests/01-clean"
[ -f "$SRC/records.txt" ] || fail "run test 01 first"
FROM="$(sed -n "s/^window: \([0-9.]*\) s$/\1/p" "$SRC/demo.log")"
python3 examples/check/crosscheck.py examples/check/i2c_sm.json "$SRC/records.txt" "$SRC/check.vcd" \
    --from "$FROM" >"$OUT/ok.txt" || fail "unchanged records must pass"
python3 - "$SRC/records.txt" "$OUT/records.txt" <<'EOF'
import sys
words = [int(x, 16) for x in open(sys.argv[1]).read().split()]
falls = [i for i, w in enumerate(words) if (w >> 24) & 0x3F == 1]
words[falls[10]] += 1
open(sys.argv[2], "w").write("".join("%08X\n" % w for w in words))
EOF
python3 examples/check/crosscheck.py examples/check/i2c_sm.json "$OUT/records.txt" "$SRC/check.vcd" \
    --from "$FROM" >"$OUT/bad.txt" && fail "a moved record passed"
grep -q "largest difference 1.000 ticks (TOO LARGE)" "$OUT/bad.txt" || fail "difference not reported"
pass "a one-tick shift is caught"
