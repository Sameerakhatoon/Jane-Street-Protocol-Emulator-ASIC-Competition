"""Two SocketCAN-style clients for tse_sim --can unix:A --can unix:B.

usage: can_client.py SOCK_A SOCK_B
Records are struct can_frame (can_id u32, len u8, 3 pad, data[8]), as a raw
CAN socket reads and writes them. A queues 0x200 (8 bytes) and 0x100, B
queues 0x0FF. Both nodes wait for the same idle bus, start in the same bit,
and 0x0FF wins arbitration; A's frame goes again afterwards. Prints what
each side received.
"""
import socket
import struct
import sys
import time


def conn(path):
    for _ in range(100):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(path)
            return s
        except OSError:
            time.sleep(0.05)
    raise SystemExit("cannot connect to " + path)


def frame(can_id, data):
    return struct.pack("<IB3x8s", can_id, len(data), data)


def recv_frames(s, n):
    got = []
    buf = b""
    s.settimeout(20)
    while len(got) < n:
        c = s.recv(16 - len(buf))
        if not c:
            raise SystemExit("closed")
        buf += c
        if len(buf) == 16:
            can_id, ln, data = struct.unpack("<IB3x8s", buf)
            got.append("%03X#%s" % (can_id, data[:ln].hex().upper()))
            buf = b""
    return got


a, b = conn(sys.argv[1]), conn(sys.argv[2])
a.sendall(frame(0x200, bytes(range(1, 9))) + frame(0x100, b"\x11\x22"))
b.sendall(frame(0x0FF, b"\xAB"))
print("A got", " ".join(recv_frames(a, 1)))
print("B got", " ".join(recv_frames(b, 2)))
