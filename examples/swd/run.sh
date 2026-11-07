#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Unmodified OpenOCD reads an SWD DPIDR through the chip's SWD probe lane:
# swd_probe_driven.tse (specs/swd_probe.tse with SWDIO driven and released
# explicitly; as shipped its image never drives SWDIO) -> tsec build at SWCLK
# 1 MHz with an 80 ns edge tolerance (the 5 MHz default is R310 and the
# shipped spec builds at 1 MHz and 40 ns) -> TSEI image -> Verilated full chip
# -> OpenOCD remote_bitbang SWD into swd_demo.py, which runs the lane's host
# steps -> an SW-DP on the lane's pads -> pins-only VCD -> SWD judge.
#
#   examples/swd/run.sh [OUT]   (default build/e2e/swd)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$ROOT/build/e2e/swd}"
# The read chains land ticks away from their ideal instants even with zero
# pad skew, and the release adds to it (60 ns of tolerance is rejected); 80 ns
# is 16 percent of the 500 ns half period, far inside SW-DP setup and hold.
SPEC="$HERE/swd_probe_driven.tse"
BUILD=(--pad-skew 0 -D f_clk=1e6 -D t_jit=80e-9)
cd "$ROOT"
mkdir -p "$OUT"

step() { printf '== %s\n' "$*"; }
die() { echo "FAIL e2e-swd: $*"; exit 1; }

command -v openocd >/dev/null || die "no openocd"
step "the default build is rejected (R310)"
st=0
python3 -m tools.tsec build specs/swd_probe.tse --pad-skew 0 -o "$OUT/default" >"$OUT/tsec-default.log" 2>&1 || st=$?
[ $st -eq 2 ] && grep -q "R310" "$OUT/tsec-default.log" || die "the 5 MHz build is no longer R310"
grep -m1 "R310" "$OUT/tsec-default.log"

step "simulator (full chip)"
make -s --no-print-directory -C sim TOP=chip >"$OUT/sim-build.log" 2>&1 || { tail -20 "$OUT/sim-build.log"; die "sim build"; }
SIM="$ROOT/sim/build/chip/tse_sim"

step "compile examples/swd/swd_probe_driven.tse ${BUILD[*]}"
rm -rf "$OUT/tsec"
python3 -m tools.tsec build "$SPEC" "${BUILD[@]}" --emit img,lst,report,json -o "$OUT/tsec" \
    >"$OUT/tsec.log" 2>&1 || { cat "$OUT/tsec.log"; die "tsec build"; }
grep -E "^RESULT" "$OUT/tsec.log"

VCD="$OUT/swd.vcd"
step "load, run, serve remote_bitbang"
: >"$OUT/demo.log"
python3 "$HERE/swd_demo.py" --sim "$SIM --pin-server --vcd $VCD --vcd-hold" --tsec "$OUT/tsec" swd_probe_driven \
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
openocd -c "adapter driver remote_bitbang; remote_bitbang host 127.0.0.1; remote_bitbang port $port" \
        -c "transport select swd; swd newdap tse cpu -expected-id 0x2ba01477" \
        -c "dap create tse.dap -chain-position tse.cpu; init" \
        -c "echo \"CTRL/STAT [tse.dap dpreg 0x4]\"" \
        -c "tse.dap dpreg 0x8 0x12345670; echo \"RDBUFF after a SELECT write [tse.dap dpreg 0xc]\"" \
        -c "shutdown" >"$OUT/openocd.log" 2>&1 || true
wait $demo || true
trap - EXIT
grep -v "^tse_sim:" "$OUT/demo.log"
grep -E "DPIDR|CTRL/STAT|RDBUFF|Error" "$OUT/openocd.log" || true
grep -q "SWD DPIDR 0x2ba01477" "$OUT/openocd.log" || die "OpenOCD read no DPIDR 0x2ba01477"
grep -q "CTRL/STAT 0xf0000001" "$OUT/openocd.log" || die "power-up handshake"
grep -q "RDBUFF after a SELECT write 0x12345670" "$OUT/openocd.log" || die "SELECT write"
grep -q "^PASS host" "$OUT/demo.log" || die "host side"

step "judge the pins (SWD)"
python3 -m verif.judges swd "$VCD" --map swclk=uio_out0 --map swdio=uio_in1 --map host_oe=uio_oe1 \
    --expect "$HERE/swd.expect" >"$OUT/judge.log" || true
cat "$OUT/judge.log"
grep -q "^PASS swd" "$OUT/judge.log" || die "SWD judge"
echo "PASS e2e-swd: OpenOCD read DPIDR 0x2ba01477, powered up the debug port and wrote SELECT" \
    "through lane 0; $(grep -vc '^#' "$HERE/swd.expect") packets judged on the pins"
