#!/usr/bin/env bash
# The compiled UART reached from a Linux pty: tsec build of the UART spec,
# image loaded into the Verilated chip, the simulator's pty bridge on the
# lane pins, a serial client on the pty, the host echoing RX lane to TX
# lane, and the judge on the pins-only VCD (recorded from RUN on).
#
#   examples/pty/run.sh [OUT]       (default build/e2e/pty)
# For picocom instead of the test client, run pty_demo.py by hand with
# --seconds 0 and open the pty it prints with: picocom -b 115200 /dev/pts/N
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$ROOT/build/e2e/pty}"
BAUD=115200
TEXT='hello from linux, through two compiled lanes\n'
cd "$ROOT"
mkdir -p "$OUT"

step() { printf '== %s\n' "$*"; }
die() { echo "FAIL e2e-pty: $*"; exit 1; }

step "simulator (full chip)"
make -s --no-print-directory -C sim TOP=chip >"$OUT/sim-build.log" 2>&1 || { tail -20 "$OUT/sim-build.log"; die "sim build"; }
SIM="$ROOT/sim/build/chip/tse_sim"

step "compile specs/uart.tse at $BAUD baud"
python3 -m tools.tsec build specs/uart.tse -D baud=$BAUD --fclk 60e6 --emit img,lst,report,json \
    -o "$OUT/tsec" >"$OUT/tsec.log" 2>&1 || { cat "$OUT/tsec.log"; die "tsec build"; }
grep -E "^RESULT" "$OUT/tsec.log"

send="$(printf "$TEXT" | od -An -tx1 | tr -d ' \n')"
n=$(( ${#send} / 2 ))
VCD="$OUT/pty.vcd"
step "load, run, open the pty, type $n bytes"
: >"$OUT/demo.log"
python3 "$HERE/pty_demo.py" --sim "$SIM --pin-server --uart --uart-tx uo0 --uart-rx uio2 --baud $BAUD --vcd $VCD --vcd-hold" \
    --tsec "$OUT/tsec" uart --count "$n" --seconds 600 >"$OUT/demo.log" 2>&1 &
demo=$!
trap 'kill $demo 2>/dev/null || true' EXIT
pty=""
for _ in $(seq 600); do
    pty="$(sed -n 's/^pty \(\/dev\/[^ ]*\)$/\1/p' "$OUT/demo.log")"
    [ -n "$pty" ] && break
    kill -0 $demo 2>/dev/null || break
    sleep 0.2
done
[ -n "$pty" ] || { cat "$OUT/demo.log"; die "no pty from the demo"; }
crc=0
python3 sim/tests/pty_client.py "$send" "$send" --pty "$pty" --timeout 300 >"$OUT/client.log" 2>&1 || crc=$?
wait $demo || true
trap - EXIT
cat "$OUT/demo.log" "$OUT/client.log"
grep -q "^PASS host" "$OUT/demo.log" || die "host side"
[ $crc -eq 0 ] || die "the client did not get its bytes back"

step "judge the RX line (uio_in2) and the chip TX pad (uo_out0)"
python3 -m verif.judges uart "$VCD" --map rx=uio_in2 --baud $BAUD --quiet --expect "$HERE/line.expect" >"$OUT/judge-rx.log" || true
cat "$OUT/judge-rx.log"
python3 -m verif.judges uart "$VCD" --map rx=uo_out0 --baud $BAUD --quiet --expect "$HERE/line.expect" >"$OUT/judge-tx.log" || true
cat "$OUT/judge-tx.log"
grep -q "^PASS uart" "$OUT/judge-rx.log" || die "RX judge"
grep -q "^PASS uart" "$OUT/judge-tx.log" || die "TX judge"
echo "PASS e2e-pty: $n bytes typed on $pty came back through two compiled lanes, both lines judged"
