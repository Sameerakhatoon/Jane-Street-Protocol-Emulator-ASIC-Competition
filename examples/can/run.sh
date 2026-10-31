#!/usr/bin/env bash
# CAN receive with the ACK in the slot, end to end: specs/can_rx_ack.tse ->
# tsec build with its timing proof -> TSEI image -> Verilated full chip
# through the pin pipe -> a CAN transmitter model on a wired-AND bus sends
# one frame with a wrong CRC, then six good ones; the lane must stay silent
# for the first and acknowledge every other one in the ACK slot and push it
# to the host -> pins-only VCD -> independent CAN judge on the bus line.
#
#   examples/can/run.sh [OUT]                 (default build/e2e/can, 500 kbit/s)
#   BITRATE=125e3 examples/can/run.sh OUT     another bit rate
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$ROOT/build/e2e/can}"
BITRATE="${BITRATE:-500e3}"
cd "$ROOT"
mkdir -p "$OUT"

step() { printf "== %s\n" "$*"; }
die() { echo "FAIL e2e-can: $*"; exit 1; }

step "simulator (full chip)"
make -s --no-print-directory -C sim TOP=chip >"$OUT/sim-build.log" 2>&1 || { tail -20 "$OUT/sim-build.log"; die "sim build"; }
SIM="$ROOT/sim/build/chip/tse_sim"

step "compile specs/can_rx_ack.tse at $BITRATE bit/s, 60 MHz"
python3 -m tools.tsec build specs/can_rx_ack.tse -D bitrate=$BITRATE --fclk 60e6 --emit img,lst,report,json \
    -o "$OUT/tsec" >"$OUT/tsec.log" 2>&1 || { cat "$OUT/tsec.log"; die "tsec build"; }
grep -E "^(== role|strategy|RESULT)|engine respond" "$OUT/tsec.log"

step "load, run, send frames"
VCD="$OUT/can.vcd"
python3 examples/can/can_demo.py --sim "$SIM --pin-server --vcd $VCD" --tsec "$OUT/tsec" can_rx_ack \
    --image "$OUT/can.tsei" --bitrate "$BITRATE" >"$OUT/demo.log" 2>&1 || true
grep -v "^tse_sim:" "$OUT/demo.log"
grep -q "^PASS host" "$OUT/demo.log" || die "host side"
FROM="$(sed -n "s/^window: \([0-9.]*\) s$/\1/p" "$OUT/demo.log")"
[ -n "$FROM" ] || die "no run window in the demo log"

step "judge the bus (ui_in6) from $FROM s"
RATE="$(python3 -c "print(int(float('$BITRATE')))")"
python3 -m verif.judges can "$VCD" --map rx=ui_in6 --bitrate "$RATE" --sample-point 0.8 --from "$FROM" \
    --expect "$HERE/can.expect" >"$OUT/judge.log" || true
cat "$OUT/judge.log"
grep -q "^PASS can" "$OUT/judge.log" || die "CAN judge"
echo "PASS e2e-can: $(grep -vc "^#" "$HERE/can.expect") frames judged at $RATE bit/s, 0 violations;" \
    "$(sed -n "s/^PASS host: .*; \(ACK driven .*\)$/\1/p" "$OUT/demo.log")"
