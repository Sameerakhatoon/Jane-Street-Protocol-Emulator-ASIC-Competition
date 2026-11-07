#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# I2C end to end, either role, Fast or Standard mode: spec -> tsec build ->
# TSEI image -> Verilated full chip through the pin pipe -> pins-only VCD ->
# I2C judge with the timing limits of the speed and the expected frames.
#
# controller: specs/i2c_controller_{fm,sm}.tse writes four bytes to a
#   simulated register target at 0x50 and reads them back (repeated START,
#   clock stretching once), and handles two NACKs (no target at 0x51, a
#   read-only register).
# target: specs/i2c_target_{fm,sm}.tse is the register target at 0x50; a
#   simulated controller writes, reads back with a repeated START, and
#   addresses 0x51, which the lane must leave unanswered.
#
#   examples/i2c/run.sh [controller|target] [fm|sm] [OUT]
#       (default controller fm, OUT build/e2e/i2c-ROLE-SPEED)
#   TR=0 examples/i2c/run.sh ...   the bus rises in the same clock it is released
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
ROLE="${1:-controller}"
SPEED="${2:-fm}"
OUT="${3:-$ROOT/build/e2e/i2c-$ROLE-$SPEED}"
cd "$ROOT"
mkdir -p "$OUT"

step() { printf "== %s\n" "$*"; }
die() { echo "FAIL e2e-i2c-$ROLE-$SPEED: $*"; exit 1; }

case "$SPEED" in
    fm) LIMIT=600000 ;;
    sm) LIMIT=2400000 ;;
    *) die "speed must be fm or sm" ;;
esac
case "$ROLE" in
    controller) DEMO=(examples/i2c/i2c_demo.py --image "$OUT/i2c.tsei"); EXPECT="$HERE/i2c.expect" ;;
    target) DEMO=(examples/i2c/target_demo.py --speed "$SPEED"); EXPECT="$HERE/target.expect" ;;
    *) die "role must be controller or target" ;;
esac
SPEC="i2c_${ROLE}_$SPEED"

step "simulator (full chip)"
make -s --no-print-directory -C sim TOP=chip >"$OUT/sim-build.log" 2>&1 || { tail -20 "$OUT/sim-build.log"; die "sim build"; }
SIM="$ROOT/sim/build/chip/tse_sim"

step "compile specs/$SPEC.tse at 60 MHz"
python3 -m tools.tsec build "specs/$SPEC.tse" --fclk 60e6 --emit img,lst,report,json \
    -o "$OUT/tsec" >"$OUT/tsec.log" 2>&1 || { cat "$OUT/tsec.log"; die "tsec build"; }
grep -E "^(== role|strategy|RESULT)" "$OUT/tsec.log"

step "load, run, drive"
VCD="$OUT/i2c.vcd"
python3 "${DEMO[@]}" --sim "$SIM --pin-server --vcd $VCD" --tsec "$OUT/tsec" "$SPEC" \
    --limit "$LIMIT" ${TR:+--tr "$TR"} >"$OUT/demo.log" 2>&1 || true
grep -v "^tse_sim:" "$OUT/demo.log"
grep -q "^PASS host" "$OUT/demo.log" || die "host side"
FROM="$(sed -n "s/^window: \([0-9.]*\) s$/\1/p" "$OUT/demo.log")"
[ -n "$FROM" ] || die "no run window in the demo log"

# Every ACK and NACK is in the expected frames, so the NACKs the demos
# provoke on purpose are checked there instead of with --require-ack.
step "judge SCL (uio_in2) and SDA (uio_in3) from $FROM s, speed $SPEED"
python3 -m verif.judges i2c "$VCD" --map scl=uio_in2 --map sda=uio_in3 --speed "$SPEED" \
    --from "$FROM" --expect "$EXPECT" >"$OUT/judge.log" || true
grep -v "^VIOLATION" "$OUT/judge.log"
{ grep "^VIOLATION" "$OUT/judge.log" | sed "s/^VIOLATION *[0-9.]* [mun]*s  //" | sort | uniq -c | sort -rn | head -5; } || true
grep -q "^PASS i2c" "$OUT/judge.log" || die "I2C judge"
echo "PASS e2e-i2c-$ROLE-$SPEED: $(grep -vc "^#" "$EXPECT") transactions judged, 0 violations"
