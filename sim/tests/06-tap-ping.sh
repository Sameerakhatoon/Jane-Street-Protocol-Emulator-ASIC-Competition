#!/usr/bin/env bash
# Linux ping through the chip: tap0 -> Manchester on ui_in[1] -> stand-in
# wire -> uo_out[1] -> FCS check -> ARP/ICMP host model -> back the same
# way -> tap0. Runs in an unprivileged user+net namespace; skipped when
# the machine has no user namespaces, no /dev/net/tun or no `ip`.
. "$(dirname "$0")/lib.sh"

command -v unshare >/dev/null || skip "no unshare"
command -v ip >/dev/null || skip "no ip"
[ -c /dev/net/tun ] || skip "no /dev/net/tun"
unshare --user --map-root-user --net true 2>/dev/null || skip "user namespaces unavailable"
build_top echo
out="$(timeout 60 unshare --user --map-root-user --net \
    bash tests/tap_inner.sh ./build/echo/tse_sim "$TMP/eth.pcap" 2>&1)" || fail "$out"
assert_contains "$out" "3 packets transmitted, 3 received" "ping"
assert_contains "$out" "0 bad" "FCS"
n="$(python3 - "$TMP/eth.pcap" <<'PY'
import struct, sys
d = open(sys.argv[1], "rb").read()
assert struct.unpack("<I", d[:4])[0] == 0xa1b2c3d4
off, n = 24, 0
while off < len(d):
    cap = struct.unpack("<IIII", d[off:off + 16])[2]
    off += 16 + cap
    n += 1
print(n)
PY
)"
[ "$n" -ge 8 ] || fail "only $n pcap records"
pass "$n pcap records"
