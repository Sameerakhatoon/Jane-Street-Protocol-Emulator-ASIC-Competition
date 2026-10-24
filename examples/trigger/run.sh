#!/usr/bin/env bash
# Trigger mode end to end: lane 0 decodes I2C bytes, lane 1 reports START
# and STOP, the 3-stage trigger fires on "write to 0x50, register 0x10,
# STOP" across both lanes; the TRIG records come back over SPI, the I2C
# judge decodes the same VCD, and the trigger-out pin is judged from it.
#
#   examples/trigger/run.sh [OUT]      (default build/check/trigger)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$ROOT/build/check/trigger}"
cd "$ROOT"
mkdir -p "$OUT"
export PYTHONDONTWRITEBYTECODE=1

step() { printf "== %s\n" "$*"; }
die() { echo "FAIL trigger: $*"; exit 1; }

step "simulator (full chip)"
make -s --no-print-directory -C sim TOP=chip >"$OUT/sim-build.log" 2>&1 || { tail -20 "$OUT/sim-build.log"; die "sim build"; }
SIM="$ROOT/sim/build/chip/tse_sim"

step "assemble the decoder"
python3 -m tools.tseasm "$HERE/i2c_bytes.s" -o "$OUT/i2c_bytes.hex" -l "$OUT/i2c_bytes.lst" || die "assembler"

step "load, run, drive the bus, pop the event log"
VCD="$OUT/trigger.vcd"
python3 "$HERE/trigger_demo.py" --sim "$SIM --pin-server --vcd $VCD" --out "$OUT" \
    >"$OUT/demo.log" 2>&1 || { grep -v "^tse_sim:" "$OUT/demo.log"; die "demo"; }
grep -v "^tse_sim:" "$OUT/demo.log"
FROM="$(sed -n "s/^window: \([0-9.]*\) s$/\1/p" "$OUT/demo.log")"
WIDTH="$(sed -n "s/^WIDTH = \([0-9]*\)$/\1/p" "$HERE/trigger_demo.py")"

step "I2C judge on the same trace"
python3 -m verif.judges i2c "$VCD" --map scl=uio_in2 --map sda=uio_in3 --speed sm --require-ack \
    --from "$FROM" --expect "$OUT/expect.txt" >"$OUT/judge.log" || { cat "$OUT/judge.log"; die "I2C judge"; }
tail -1 "$OUT/judge.log"

step "trigger-out pin (uo_out7) from the VCD"
python3 "$HERE/trigpin.py" "$VCD" --from "$FROM" --width "$WIDTH" >"$OUT/trigpin.txt" \
    || { cat "$OUT/trigpin.txt"; die "trigger pin"; }
cat "$OUT/trigpin.txt"
echo "PASS trigger: $(grep -c . "$OUT/trig.txt") fires, TRIG records and pin agree with the decoded trace"
