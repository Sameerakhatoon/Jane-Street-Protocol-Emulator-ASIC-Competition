#!/usr/bin/env bash
# Both lanes at once: specs/uart_tx.tse on lane 0 (banner on uo_out[0]) and
# specs/i2c_controller_fm.tse on lane 1 (write and read back a register
# target on uio[2], uio[3]), one image, one run. Judged per line; the UART
# edges must sit at the compiler cycles while the other lane runs.
#
#   examples/combo/run.sh [OUT]      (default build/e2e/combo)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$ROOT/build/e2e/combo}"
BAUD=115200
TX_TEXT='Two lanes: UART and I2C\r\n'
cd "$ROOT"
mkdir -p "$OUT"

step() { printf '== %s\n' "$*"; }
die() { echo "FAIL e2e-combo: $*"; exit 1; }

step "simulator (full chip)"
make -s --no-print-directory -C sim TOP=chip >"$OUT/sim-build.log" 2>&1 || { tail -20 "$OUT/sim-build.log"; die "sim build"; }
SIM="$ROOT/sim/build/chip/tse_sim"

step "compile specs/uart_tx.tse and specs/i2c_controller_fm.tse at 60 MHz"
for s in uart_tx i2c_controller_fm; do
    python3 -m tools.tsec build specs/$s.tse --fclk 60e6 --emit img,lst,report,json \
        -o "$OUT/$s" >"$OUT/$s.log" 2>&1 || { cat "$OUT/$s.log"; die "tsec build $s"; }
    grep -E "^(== role|RESULT)" "$OUT/$s.log"
done

step "load, run, drive"
VCD="$OUT/combo.vcd"
python3 examples/combo/combo_demo.py --sim "$SIM --pin-server --vcd $VCD" --uart "$OUT/uart_tx" uart_tx \
    --i2c "$OUT/i2c_controller_fm" i2c_controller_fm --image "$OUT/combo.tsei" --tx-text "$TX_TEXT" \
    >"$OUT/demo.log" 2>&1 || true
grep -v "^tse_sim:" "$OUT/demo.log"
grep -q "^PASS host" "$OUT/demo.log" || die "host side"
FROM="$(sed -n 's/^window: \([0-9.]*\) s$/\1/p' "$OUT/demo.log")"

step "judge lane 0 UART (uo_out0), its timing, and lane 1 I2C (uio_in2, uio_in3)"
python3 -m verif.judges uart "$VCD" --map rx=uo_out0 --baud $BAUD --from "$FROM" --quiet \
    --expect "$HERE/tx.expect" >"$OUT/judge-uart.log" || true
cat "$OUT/judge-uart.log"
python3 examples/uart/timing.py --report "$OUT/uart_tx/uart_tx.report.txt" --role tx_side \
    --vcd "$VCD" --pin uo_out0 --from "$FROM" --baud $BAUD >"$OUT/timing.log" || true
cat "$OUT/timing.log"
python3 -m verif.judges i2c "$VCD" --map scl=uio_in2 --map sda=uio_in3 --speed fm --require-ack \
    --from "$FROM" --expect "$HERE/../i2c/i2c.expect" >"$OUT/judge-i2c.log" || true
grep -v "^VIOLATION" "$OUT/judge-i2c.log"
{ grep "^VIOLATION" "$OUT/judge-i2c.log" | sed 's/^VIOLATION *[0-9.]* [mun]*s  //' | sort | uniq -c | sort -rn | head -5; } || true
grep -q "^PASS uart" "$OUT/judge-uart.log" || die "UART judge"
grep -q "^PASS timing" "$OUT/timing.log" || die "UART timing"
grep -q "^PASS i2c" "$OUT/judge-i2c.log" || die "I2C judge"
echo "PASS e2e-combo: UART on lane 0 and I2C on lane 1 judged, 0 violations"
