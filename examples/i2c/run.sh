#!/usr/bin/env bash
# I2C controller (Fast mode) end to end: specs/i2c_controller_fm.tse ->
# tsec build -> TSEI image -> Verilated full chip through the pin pipe ->
# the lane writes four bytes to a simulated register target at 0x50 and
# reads them back (repeated START, clock stretching once) -> pins-only VCD
# -> I2C judge with the Fast-mode timing limits and every byte ACKed.
#
#   examples/i2c/run.sh [OUT]      (default build/e2e/i2c)
#   TR=0 examples/i2c/run.sh OUT   the bus rises in the same clock it is released
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$ROOT/build/e2e/i2c}"
cd "$ROOT"
mkdir -p "$OUT"

step() { printf "== %s\n" "$*"; }
die() { echo "FAIL e2e-i2c: $*"; exit 1; }

step "simulator (full chip)"
make -s --no-print-directory -C sim TOP=chip >"$OUT/sim-build.log" 2>&1 || { tail -20 "$OUT/sim-build.log"; die "sim build"; }
SIM="$ROOT/sim/build/chip/tse_sim"

step "compile specs/i2c_controller_fm.tse at 60 MHz"
python3 -m tools.tsec build specs/i2c_controller_fm.tse --fclk 60e6 --emit img,lst,report,json \
    -o "$OUT/tsec" >"$OUT/tsec.log" 2>&1 || { cat "$OUT/tsec.log"; die "tsec build"; }
grep -E "^(== role|strategy|RESULT)" "$OUT/tsec.log"

step "load, run, drive"
VCD="$OUT/i2c.vcd"
python3 examples/i2c/i2c_demo.py --sim "$SIM --pin-server --vcd $VCD" --tsec "$OUT/tsec" i2c_controller_fm \
    --image "$OUT/i2c.tsei" ${TR:+--tr "$TR"} >"$OUT/demo.log" 2>&1 || true
grep -v "^tse_sim:" "$OUT/demo.log"
grep -q "^PASS host" "$OUT/demo.log" || die "host side"
FROM="$(sed -n "s/^window: \([0-9.]*\) s$/\1/p" "$OUT/demo.log")"
[ -n "$FROM" ] || die "no run window in the demo log"

step "judge SCL (uio_in2) and SDA (uio_in3) from $FROM s, Fast mode"
python3 -m verif.judges i2c "$VCD" --map scl=uio_in2 --map sda=uio_in3 --speed fm --require-ack \
    --from "$FROM" --expect "$HERE/i2c.expect" >"$OUT/judge.log" || true
grep -v "^VIOLATION" "$OUT/judge.log"
{ grep "^VIOLATION" "$OUT/judge.log" | sed "s/^VIOLATION *[0-9.]* [mun]*s  //" | sort | uniq -c | sort -rn | head -5; } || true
grep -q "^PASS i2c" "$OUT/judge.log" || die "I2C judge"
echo "PASS e2e-i2c: $(grep -vc "^#" "$HERE/i2c.expect") transactions judged, 0 violations"
