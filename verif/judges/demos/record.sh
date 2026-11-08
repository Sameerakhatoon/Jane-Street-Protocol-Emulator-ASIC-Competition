#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Record the pins-only traces of the stand-in demos for stand_in.txt:
# real OpenOCD over remote_bitbang (JTAG IDCODE, SWD DPIDR and CTRL/STAT)
# and Linux ping through a TAP device (no root, a user namespace).
#
#   bash verif/judges/demos/record.sh [OUTDIR]     (default build/demos)
#
# A demo whose tool is missing writes no trace; the manifest then reports
# it as SKIP, which counts as a failure.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
OUT="$(mkdir -p "${1:-$ROOT/build/demos}" && cd "${1:-$ROOT/build/demos}" && pwd)"
SIM="$ROOT/sim/build/echo/tse_sim"

make -s --no-print-directory -C "$ROOT/sim" TOP=echo >"$OUT/build.log" 2>&1 || { tail -20 "$OUT/build.log"; exit 1; }
rm -f "$OUT"/*.vcd

# start_sim LOG ARGS...: run tse_sim in the background, wait for its rbb port
start_sim(){
    local log=$1; shift
    "$SIM" "$@" >"$log" 2>&1 &
    sim=$!
    for _ in $(seq 50); do
        port="$(sed -n 's/^rbb 127.0.0.1:\([0-9]*\)/\1/p' "$log")"
        [ -n "$port" ] && return 0
        sleep 0.1
    done
    return 1
}

stop_sim(){
    # OpenOCD has quit, but the simulator may still be clocking its last
    # buffered operations (the line reset sent on shutdown): wait until it
    # has closed the connection, then stop it.
    for _ in $(seq 100); do
        ss -Htn state close-wait "( sport = :$port )" 2>/dev/null | grep -q . || break
        sleep 0.1
    done
    kill -INT "$sim" 2>/dev/null
    wait "$sim" 2>/dev/null
}

RBB='adapter driver remote_bitbang; remote_bitbang host 127.0.0.1'
if command -v openocd >/dev/null; then
    start_sim "$OUT/jtag.log" --rbb 0 --vcd "$OUT/jtag.vcd"
    openocd -c "$RBB; remote_bitbang port $port" \
            -c "transport select jtag; jtag newtap tse tap -irlen 4 -expected-id 0x15e00001" \
            -c "init; shutdown" >>"$OUT/jtag.log" 2>&1
    stop_sim
    start_sim "$OUT/swd.log" --rbb 0 --vcd "$OUT/swd.vcd"
    openocd -c "$RBB; remote_bitbang port $port" \
            -c "transport select swd; swd newdap tse cpu -expected-id 0x0bc11477" \
            -c "dap create tse.dap -chain-position tse.cpu; init" \
            -c "echo \"CTRL/STAT [tse.dap dpreg 0x4]\"; shutdown" >>"$OUT/swd.log" 2>&1
    stop_sim
    grep -h "tap/device found\|DPIDR\|CTRL/STAT\|clashes" "$OUT/jtag.log" "$OUT/swd.log"
else
    echo "record: no openocd, JTAG and SWD traces skipped"
fi

if unshare --user --map-root-user --net true 2>/dev/null && [ -e /dev/net/tun ]; then
    unshare --user --map-root-user --net bash -c "
        ip tuntap add dev tap0 mode tap && ip addr add 10.0.0.1/24 dev tap0 && ip link set tap0 up || exit 1
        '$SIM' --tap tap0 --vcd '$OUT/eth.vcd' 2>'$OUT/eth.log' & sleep 0.5
        ping -c 3 -W 2 10.0.0.2
        kill -INT %1; wait" >"$OUT/ping.log" 2>&1
    grep -h "received\|eth:" "$OUT/ping.log" "$OUT/eth.log"
else
    echo "record: no user namespace or /dev/net/tun, TAP trace skipped"
fi
ls "$OUT"/*.vcd 2>/dev/null
