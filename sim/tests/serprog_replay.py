"""Replays flashrom's serprog init (programmers/serprog.c), a JEDEC ID and a read.

usage: serprog_replay.py PORT
Stands in for `flashrom -p serprog:ip=127.0.0.1:PORT` until flashrom is installed.
"""
import socket
import struct
import sys
import time

s = socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=10)
s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


def rx(n):
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise SystemExit("connection closed")
        b += c
    return b


def cmd(op, params=b"", retlen=0):
    s.sendall(bytes([op]) + params)
    ack = rx(1)[0]
    assert ack == 0x06, f"cmd {op:#x}: got {ack:#x}"
    return rx(retlen)


def le24(n):
    return struct.pack("<I", n)[:3]


s.sendall(b"\x00" * 8)                       # sp_synchronize: 8 NOPs
assert rx(8) == b"\x06" * 8
s.sendall(b"\x10")                           # SYNCNOP -> NAK, ACK
assert rx(2) == b"\x15\x06"
assert struct.unpack("<H", cmd(0x01, retlen=2))[0] == 1
cmdmap = cmd(0x02, retlen=32)
assert cmdmap[0x13 // 8] & (1 << (0x13 % 8)), "O_SPIOP missing from command map"
assert cmd(0x05, retlen=1)[0] & 0x08, "SPI bus not advertised"
cmd(0x12, b"\x08")
assert struct.unpack("<I", cmd(0x14, struct.pack("<I", 1000000), retlen=4))[0] == 1000000
print("programmer name:", cmd(0x03, retlen=16).rstrip(b"\0").decode())
s.sendall(b"\x42")                           # unknown command -> NAK
assert rx(1) == b"\x15"
t0 = time.time()
jedec = cmd(0x13, le24(1) + le24(3) + b"\x9f", retlen=3)
data = cmd(0x13, le24(4) + le24(16) + b"\x03\x00\x01\x00", retlen=16)
print("JEDEC ID:", jedec.hex(), "read:", data.hex(), f"{(time.time() - t0) * 1e3:.1f} ms")
assert jedec == bytes([0xEF, 0x40, 0x18])
assert data == bytes(range(16))
print("PASS")
