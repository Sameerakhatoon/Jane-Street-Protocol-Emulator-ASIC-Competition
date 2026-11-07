#!/usr/bin/env bash
# Unmodified OpenOCD reads a JTAG IDCODE through the chip's JTAG probe lane:
# specs/jtag_probe.tse -> tsec build (--pad-skew 0, R310 without it) -> TSEI
# image -> Verilated full chip -> OpenOCD remote_bitbang into jtag_demo.py,
# which feeds the lane 16 TMS/TDI pairs per mailbox word -> a TAP on the
# lane's pads -> pins-only VCD -> independent JTAG judge.
#
#   examples/jtag/run.sh [OUT]   (default build/e2e/jtag)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$ROOT/build/e2e/jtag}"
cd "$ROOT"
mkdir -p "$OUT"

step() { printf '== %s\n' "$*"; }
die() { echo "FAIL e2e-jtag: $*"; exit 1; }

command -v openocd >/dev/null || die "no openocd"
step "simulator (full chip)"
make -s --no-print-directory -C sim TOP=chip >"$OUT/sim-build.log" 2>&1 || { tail -20 "$OUT/sim-build.log"; die "sim build"; }
SIM="$ROOT/sim/build/chip/tse_sim"

step "compile specs/jtag_probe.tse (TCK 5 MHz, --pad-skew 0)"
python3 -m tools.tsec build specs/jtag_probe.tse --pad-skew 0 --emit img,lst,report,json -o "$OUT/tsec" \
    >"$OUT/tsec.log" 2>&1 || { cat "$OUT/tsec.log"; die "tsec build"; }
grep -E "^RESULT" "$OUT/tsec.log"

VCD="$OUT/jtag.vcd"
step "load, run, serve remote_bitbang"
: >"$OUT/demo.log"
python3 "$HERE/jtag_demo.py" --sim "$SIM --pin-server --vcd $VCD --vcd-hold" --tsec "$OUT/tsec" jtag_probe \
    >"$OUT/demo.log" 2>&1 &
demo=$!
trap 'kill $demo 2>/dev/null || true' EXIT
port=""
for _ in $(seq 600); do
    port="$(sed -n 's/^rbb 127.0.0.1:\([0-9]*\)$/\1/p' "$OUT/demo.log")"
    [ -n "$port" ] && break
    kill -0 $demo 2>/dev/null || break
    sleep 0.2
done
[ -n "$port" ] || { cat "$OUT/demo.log"; die "no remote_bitbang port from the demo"; }
step "openocd"
start=$(date +%s.%N)
openocd -c "adapter driver remote_bitbang; remote_bitbang host 127.0.0.1; remote_bitbang port $port" \
        -c "transport select jtag; jtag newtap tse dap -irlen 4 -expected-id 0x4ba00477" \
        -c "init" \
        -c "irscan tse.dap 0xe; echo \"IDCODE by DR scan: 0x[drscan tse.dap 32 0]\"" \
        -c "irscan tse.dap 0xf; echo \"BYPASS 0xa5 in, 9 bits: 0x[drscan tse.dap 9 0xa5]\"" \
        -c "shutdown" >"$OUT/openocd.log" 2>&1 || true
wait $demo || true
trap - EXIT
grep -v "^tse_sim:" "$OUT/demo.log"
grep -E "tap/device found|IDCODE by|BYPASS 0x|Error|clashes" "$OUT/openocd.log" || true
printf 'openocd wall time: %.1f s\n' "$(echo "$(date +%s.%N) - $start" | bc)"
grep -q "tap/device found: 0x4ba00477" "$OUT/openocd.log" || die "OpenOCD found no TAP with IDCODE 0x4ba00477"
grep -q "IDCODE by DR scan: 0x4ba00477" "$OUT/openocd.log" || die "IDCODE DR scan"
grep -q "BYPASS 0xa5 in, 9 bits: 0x014a" "$OUT/openocd.log" || die "BYPASS scan"
grep -q "^PASS host" "$OUT/demo.log" || die "host side"

step "judge the pins (JTAG)"
python3 -m verif.judges jtag "$VCD" --map tck=uio_out0 --map tms=uio_out1 --map tdi=uio_out2 --map tdo=uio_in3 \
    --expect "$HERE/jtag.expect" >"$OUT/judge.log" || true
cat "$OUT/judge.log"
grep -q "^PASS jtag" "$OUT/judge.log" || die "JTAG judge"
echo "PASS e2e-jtag: OpenOCD found IDCODE 0x4ba00477 through lane 0, read it again by DR scan and" \
    "shifted BYPASS; $(grep -vc '^#' "$HERE/jtag.expect") scans judged on the pins"
