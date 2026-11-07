#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# The Linux kernel answers an ARP request and an ICMP echo request sent by
# the 10BASE-T transmit lane: eth10_tx_window compiled, loaded into the
# Verilated full chip, its td pad decoded by tse_sim's TAP bridge into tap0
# of a user and network namespace (no root). The kernel's replies are coded
# onto the chip's receive pin, where no lane listens (eth10_rx.tse is R314).
# The judge reads the transmit line; ping.expect holds the frames.
#
#   examples/eth10/run_ping.sh [OUT]   (default build/e2e/eth10-ping)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$ROOT/build/e2e/eth10-ping}"
SPEC=eth10_tx_window
FRAMES="$HERE/ping.expect"
cd "$ROOT"
mkdir -p "$OUT"

step() { printf '== %s\n' "$*"; }
die() { echo "FAIL e2e-eth10-ping: $*"; exit 1; }

unshare --user --map-root-user --net true 2>/dev/null && [ -e /dev/net/tun ] \
    || die "no user namespace or /dev/net/tun"
step "simulator (full chip)"
make -s --no-print-directory -C sim TOP=chip >"$OUT/sim-build.log" 2>&1 || { tail -20 "$OUT/sim-build.log"; die "sim build"; }
SIM="$ROOT/sim/build/chip/tse_sim"

step "compile $SPEC"
python3 -m tools.tsec build "tools/tsec/tests/targets/$SPEC.tse" --emit img,lst,report,json -o "$OUT/tsec" \
    >"$OUT/tsec.log" 2>&1 || { cat "$OUT/tsec.log"; die "tsec build"; }
grep -E "^RESULT" "$OUT/tsec.log"

step "tap0 in a namespace; load, run, send the frames"
rm -f "$OUT/tap.pcap"
unshare --user --map-root-user --net bash -c "
    ip tuntap add dev tap0 mode tap && ip link set tap0 address 02:00:00:00:00:01 || exit 1
    echo 1 >/proc/sys/net/ipv6/conf/tap0/disable_ipv6 2>/dev/null
    ip addr add 10.0.0.1/24 dev tap0 && ip link set tap0 up || exit 1
    python3 '$HERE/ping_demo.py' --tsec '$OUT/tsec' $SPEC --vcd '$OUT/pins.vcd' --line '$OUT/line.vcd' \
        --pcap '$OUT/tap.pcap' --frames '$FRAMES' \
        --sim '$SIM --pin-server --tap tap0 --tap-pins uo1,uio5 --pcap $OUT/tap.pcap --vcd $OUT/pins.vcd --vcd-hold'
    grep '^Icmp:' /proc/net/snmp >'$OUT/icmp.txt'" >"$OUT/demo.log" 2>&1 || true
grep -v "^tse_sim:" "$OUT/demo.log"
grep -q "^PASS host" "$OUT/demo.log" || die "host side"
icmp="$(python3 -c 'import sys; h, v = [l.split()[1:] for l in open(sys.argv[1])]; d = dict(zip(h, v))
print(" ".join("%s %s" % (k, d[k]) for k in ("InMsgs", "InEchos", "OutEchoReps")))' "$OUT/icmp.txt")"
echo "kernel ICMP counters: $icmp"
[[ "$icmp" == *"InEchos 1 OutEchoReps 1" ]] || die "kernel ICMP counters"

step "judge the transmit line (10BASE-T)"
python3 -m verif.judges eth10 "$OUT/line.vcd" --map td=line --expect "$FRAMES" >"$OUT/judge.log" || true
cat "$OUT/judge.log"
grep -q "^PASS eth10" "$OUT/judge.log" || die "10BASE-T judge"
echo "PASS e2e-eth10-ping: 2 frames from the transmit lane judged; the kernel answered the ARP request" \
    "and the ICMP echo request"
