#!/usr/bin/env bash
# UART end to end: timed spec -> tsec build with its timing proof -> TSEI
# image -> loaded over the byte bus into the Verilated full chip through
# the pin pipe -> lanes run from SRAM -> host traffic over SPI, RX frames on
# a pin -> pins-only VCD -> independent judge. PASS only if every step does.
#
#   examples/uart/run.sh [OUT]      (default build/e2e/uart; run from anywhere)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$ROOT/build/e2e/uart}"
BAUD=115200
FCLK=60e6
TX_TEXT='Hello from TSE\r\n'
RX_TEXT='U\x00\xff\nTSE rx'
cd "$ROOT"
mkdir -p "$OUT"

step() { printf '== %s\n' "$*"; }
die() { echo "FAIL e2e-uart: $*"; exit 1; }

step "simulator (full chip)"
make -s --no-print-directory -C sim TOP=chip >"$OUT/sim-build.log" 2>&1 || { tail -20 "$OUT/sim-build.log"; die "sim build"; }
SIM="$ROOT/sim/build/chip/tse_sim"

step "compile specs/uart.tse at $BAUD baud, $FCLK Hz"
python3 -m tools.tsec build specs/uart.tse -D baud=$BAUD --fclk $FCLK --emit img,lst,report,json \
    -o "$OUT/tsec" >"$OUT/tsec.log" 2>&1 || { cat "$OUT/tsec.log"; die "tsec build"; }
grep -E "^(== role|strategy|ACCEPTED|RESULT)" "$OUT/tsec.log"

step "load, run, drive"
VCD="$OUT/uart.vcd"
python3 examples/uart/uart_demo.py --sim "$SIM --pin-server --vcd $VCD" --tsec "$OUT/tsec" uart \
    --image "$OUT/uart.tsei" --baud $BAUD --tx-text "$TX_TEXT" --rx-text "$RX_TEXT" \
    >"$OUT/demo.log" 2>&1 || true
grep -v "^tse_sim:" "$OUT/demo.log"
grep -q "^PASS host" "$OUT/demo.log" || die "host side"
python3 host/tse_cli.py image show "$OUT/uart.tsei" >"$OUT/image.txt"
FROM="$(sed -n 's/^window: \([0-9.]*\) s$/\1/p' "$OUT/demo.log")"
[ -n "$FROM" ] || die "no run window in the demo log"

step "judge chip TX (uo_out0) and the RX line (uio_in2) from $FROM s"
python3 -m verif.judges uart "$VCD" --map rx=uo_out0 --baud $BAUD --from "$FROM" --quiet \
    --expect "$HERE/tx.expect" >"$OUT/judge-tx.log" || true
cat "$OUT/judge-tx.log"
python3 -m verif.judges uart "$VCD" --map rx=uio_in2 --baud $BAUD --from "$FROM" --quiet \
    --expect "$HERE/rx.expect" >"$OUT/judge-rx.log" || true
cat "$OUT/judge-rx.log"
grep -q "^PASS uart" "$OUT/judge-tx.log" || die "TX judge"
grep -q "^PASS uart" "$OUT/judge-rx.log" || die "RX judge"

step "timing: compiler report against the measured TX edges"
python3 examples/uart/timing.py --report "$OUT/tsec/uart.report.txt" --role tx_side \
    --vcd "$VCD" --pin uo_out0 --from "$FROM" --baud $BAUD >"$OUT/timing.log" || true
cat "$OUT/timing.log"
grep -q "^PASS timing" "$OUT/timing.log" || die "timing"

echo "PASS e2e-uart: $(grep -vc "^#" "$HERE/tx.expect") TX frames, $(grep -vc "^#" "$HERE/rx.expect") RX frames judged, 0 violations"
