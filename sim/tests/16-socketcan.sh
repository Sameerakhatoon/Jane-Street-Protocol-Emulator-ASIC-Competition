#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Real SocketCAN: node A on vcan0, node B on a can_frame socket. A frame sent
# with a raw AF_CAN socket on vcan0 crosses the simulated bus to B, and B's
# frame comes back on vcan0. Needs the vcan module and an interface that is
# up (as root: modprobe vcan; ip link add vcan0 type vcan; ip link set vcan0 up).
. "$(dirname "$0")/lib.sh"

python3 -c 'import socket; socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)' 2>/dev/null \
    || skip "no AF_CAN sockets in this kernel"
ip link show vcan0 >/dev/null 2>&1 || skip "no vcan0 (the vcan module is not in this kernel)"

build_top echo
./build/echo/tse_sim --can vcan0 --can "unix:$TMP/b" >"$TMP/out" 2>"$TMP/err" &
pid=$!
trap 'kill $pid 2>/dev/null || true; rm -rf "$TMP"' EXIT
out="$(timeout 30 python3 - "$TMP/b" <<'EOF' 2>&1
import socket, struct, sys, time
v = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
v.bind(("vcan0",))
v.settimeout(10)
for _ in range(100):
    try:
        b = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        b.connect(sys.argv[1])
        break
    except OSError:
        time.sleep(0.05)
b.settimeout(10)
v.send(struct.pack("<IB3x8s", 0x123, 4, b"\xde\xad\xbe\xef"))
can_id, ln, data = struct.unpack("<IB3x8s", b.recv(16))
print("B got %03X#%s" % (can_id, data[:ln].hex().upper()))
b.sendall(struct.pack("<IB3x8s", 0x321, 1, b"\x42"))
can_id, ln, data = struct.unpack("<IB3x8s", v.recv(16))
print("vcan0 got %03X#%s" % (can_id, data[:ln].hex().upper()))
EOF
)" || fail "$out $(cat "$TMP/err")"
assert_contains "$out" "B got 123#DEADBEEF" "vcan0 to bus"
assert_contains "$out" "vcan0 got 321#42" "bus to vcan0"
pass
