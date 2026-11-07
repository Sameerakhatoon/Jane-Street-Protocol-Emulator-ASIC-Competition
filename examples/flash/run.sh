#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# A serial NOR flash compiled from a timed spec, read over flashrom's serprog
# protocol. The flash runs on a lane; the SPI controller is either a stand-in
# made of input pins or (--lanes) a second compiled lane, so both sides of
# the bus are TSE lanes. The serprog client is real flashrom when installed,
# else the replay of flashrom's traffic in sim/tests. The SPI judge checks
# every chip-select frame on the pins.
#
#   examples/flash/run.sh [--lanes] [OUT]   (default build/e2e/flash or flash-lanes)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
LANES=0
if [ "${1:-}" = "--lanes" ]; then
    LANES=1
    shift
fi
NAME=flash
[ $LANES -eq 1 ] && NAME=flash-lanes
OUT="${1:-$ROOT/build/e2e/$NAME}"
# The flash lane needs up to 40 cycles from the last command rise to its
# first MISO wait (the opcode decode, the push and the pull), so the SCK half
# period must be longer than that: 500 kHz gives 60 cycles at 60 MHz.
CTL_HZ=5e5
CTL_RD=3.3333e-8                # MISO sample two cycles before the fall, a whole cycle count
SPISPEED="${SPISPEED:-500000}"
cd "$ROOT"
mkdir -p "$OUT"

step() { printf '== %s\n' "$*"; }
die() { echo "FAIL e2e-$NAME: $*"; exit 1; }

step "simulator (full chip)"
make -s --no-print-directory -C sim TOP=chip >"$OUT/sim-build.log" 2>&1 || { tail -20 "$OUT/sim-build.log"; die "sim build"; }
SIM="$ROOT/sim/build/chip/tse_sim"

step "compile specs/spi_flash_target.tse (SCK up to 500 kHz)"
python3 -m tools.tsec build specs/spi_flash_target.tse --emit img,lst,report,json \
    -o "$OUT/flash" >"$OUT/tsec-flash.log" 2>&1 || { cat "$OUT/tsec-flash.log"; die "tsec build (flash)"; }
grep -E "^RESULT" "$OUT/tsec-flash.log"
CTL=()
JUDGE=(--map cs=ui_in4 --map clk=ui_in5 --map mosi=ui_in6 --map miso=uo_out1)
if [ $LANES -eq 1 ]; then
    step "compile specs/spi_controller_mode0.tse at $CTL_HZ Hz SCK, t_rd $CTL_RD s, --pad-skew 0"
    python3 -m tools.tsec build specs/spi_controller_mode0.tse -D f_sck=$CTL_HZ -D t_rd=$CTL_RD -D t_css=250e-9 --pad-skew 0 \
        --emit img,lst,report,json -o "$OUT/ctl" >"$OUT/tsec-ctl.log" 2>&1 \
        || { cat "$OUT/tsec-ctl.log"; die "tsec build (controller)"; }
    grep -E "^RESULT" "$OUT/tsec-ctl.log"
    CTL=(--ctl "$OUT/ctl" spi_controller_mode0 --ctl-hz $CTL_HZ)
    JUDGE=(--map cs=uio_out4 --map clk=uio_out5 --map mosi=uio_out6 --map miso=uio_out7)
fi

VCD="$OUT/$NAME.vcd"
step "load, run, serve serprog"
: >"$OUT/demo.log"
python3 "$HERE/flash_demo.py" --sim "$SIM --pin-server --vcd $VCD --vcd-hold" \
    --flash "$OUT/flash" spi_flash_target "${CTL[@]}" >"$OUT/demo.log" 2>&1 &
demo=$!
trap 'kill $demo 2>/dev/null || true' EXIT
port=""
for _ in $(seq 600); do
    port="$(sed -n 's/^serprog 127.0.0.1:\([0-9]*\)$/\1/p' "$OUT/demo.log")"
    [ -n "$port" ] && break
    kill -0 $demo 2>/dev/null || break
    sleep 0.2
done
[ -n "$port" ] || { cat "$OUT/demo.log"; die "no serprog port from the demo"; }
crc=0
if command -v flashrom >/dev/null; then
    flashrom -p "serprog:ip=127.0.0.1:$port,spispeed=$SPISPEED" >"$OUT/client.log" 2>&1 || crc=$?
else
    python3 sim/tests/serprog_replay.py "$port" --id ef4015 --spispeed "$SPISPEED" --status --timeout 600 \
        >"$OUT/client.log" 2>&1 || crc=$?
fi
wait $demo || true
trap - EXIT
cat "$OUT/client.log" "$OUT/demo.log"
[ $crc -eq 0 ] || die "serprog client"
grep -q "^PASS host" "$OUT/demo.log" || die "host side"

step "judge every chip-select frame (SPI mode 0)"
python3 -m verif.judges spi "$VCD" "${JUDGE[@]}" --mode 0 --quiet --expect "$HERE/flash.expect" \
    >"$OUT/judge.log" || true
cat "$OUT/judge.log"
grep -q "^PASS spi" "$OUT/judge.log" || die "SPI judge"
echo "PASS e2e-$NAME: JEDEC ID, a 16-byte read and the status register over serprog, judged on the pins"
