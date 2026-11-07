#!/usr/bin/env bash
# CAN receiver lane against the simulator's bit-level CAN node: specs/can_rx_ack.tse
# at 500 kbit/s (1 Mbit/s is R311) -> tsec build -> Verilated full chip -> tse_sim
# --can unix:PATH at 120 clocks per bit, fed struct can_frame records as a raw
# SocketCAN socket carries them -> the lane acknowledges each frame in its ACK slot
# and pushes it -> pins-only VCD -> CAN judge on the bus wire.
#
# This WSL kernel has no vcan module (CONFIG_CAN_VCAN is not set), so cansend on
# vcan0 cannot be the client; on a kernel with vcan, --can vcan0 is the same node.
#
#   examples/can/run_node.sh [OUT]   (default build/e2e/can-node)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$ROOT/build/e2e/can-node}"
BITRATE=500e3
cd "$ROOT"
mkdir -p "$OUT"

step() { printf '== %s\n' "$*"; }
die() { echo "FAIL e2e-can-node: $*"; exit 1; }

step "SocketCAN in this kernel"
python3 -c 'import socket; socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)' 2>/dev/null \
    && echo "AF_CAN raw sockets: yes" || echo "AF_CAN raw sockets: no"
if unshare --user --map-root-user --net ip link add vcan0 type vcan 2>/dev/null; then
    echo "vcan: yes"
else
    echo "vcan: no (the kernel has no vcan module), the node takes can_frame records on a Unix socket"
fi

step "simulator (full chip)"
make -s --no-print-directory -C sim TOP=chip >"$OUT/sim-build.log" 2>&1 || { tail -20 "$OUT/sim-build.log"; die "sim build"; }
SIM="$ROOT/sim/build/chip/tse_sim"

step "compile specs/can_rx_ack.tse at $BITRATE bit/s"
python3 -m tools.tsec build specs/can_rx_ack.tse -D bitrate=$BITRATE --emit img,lst,report,json \
    -o "$OUT/tsec" >"$OUT/tsec.log" 2>&1 || { cat "$OUT/tsec.log"; die "tsec build"; }
grep -E "^RESULT" "$OUT/tsec.log"

step "load, run, send can_frame records through the CAN node"
VCD="$OUT/can.vcd"
BIT="$(python3 -c "print(int(60e6 / $BITRATE))")"
python3 "$HERE/node_demo.py" --tsec "$OUT/tsec" can_rx_ack --bitrate $BITRATE \
    --sim "$SIM --pin-server --can unix:$OUT/can.sock --can-pins uio7,uio6 --can-bit $BIT --vcd $VCD --vcd-hold" \
    >"$OUT/demo.log" 2>&1 || true
grep -v "^tse_sim:" "$OUT/demo.log"
grep -q "^PASS host" "$OUT/demo.log" || die "host side"

step "judge the bus"
RATE="$(python3 -c "print(int(float('$BITRATE')))")"
python3 -m verif.judges can "$VCD" --map rx=can --bitrate "$RATE" --sample-point 0.8 \
    --expect "$HERE/can.expect" >"$OUT/judge.log" || true
cat "$OUT/judge.log"
grep -q "^PASS can" "$OUT/judge.log" || die "CAN judge"
echo "PASS e2e-can-node: $(grep -vc "^#" "$HERE/can.expect") frames from the CAN node acknowledged by" \
    "lane 0 and judged at $RATE bit/s"
