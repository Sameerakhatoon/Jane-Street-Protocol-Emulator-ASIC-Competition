"""Replays flashrom's serprog init (programmers/serprog.c), a JEDEC ID and a read.

usage: serprog_replay.py PORT [--id HEX] [--spispeed HZ] [--status] [--timeout S]
Stands in for `flashrom -p serprog:ip=127.0.0.1:PORT,spispeed=HZ` until
flashrom is installed. --id is the JEDEC ID to expect (ef4018); --status
also reads the status register, sets the write enable latch (06) and reads
it again, expecting 00 then 02. The programmer may answer the SPI clock
request with a lower rate, as flashrom allows.
"""
import argparse
import socket
import struct
import time

ap = argparse.ArgumentParser()
ap.add_argument("port", type=int)
ap.add_argument("--id", default="ef4018")
ap.add_argument("--spispeed", type=int, default=1000000)
ap.add_argument("--status", action="store_true")
ap.add_argument("--timeout", type=float, default=10)
a = ap.parse_args()
s = socket.create_connection(("127.0.0.1", a.port), timeout=a.timeout)
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
hz = struct.unpack("<I", cmd(0x14, struct.pack("<I", a.spispeed), retlen=4))[0]
assert 0 < hz <= a.spispeed, f"SPI clock {hz} Hz for a request of {a.spispeed}"
print("SPI clock:", hz, "Hz")
print("programmer name:", cmd(0x03, retlen=16).rstrip(b"\0").decode())
s.sendall(b"\x42")                           # unknown command -> NAK
assert rx(1) == b"\x15"
t0 = time.time()
jedec = cmd(0x13, le24(1) + le24(3) + b"\x9f", retlen=3)
data = cmd(0x13, le24(4) + le24(16) + b"\x03\x00\x01\x00", retlen=16)
print("JEDEC ID:", jedec.hex(), "read:", data.hex(), f"{(time.time() - t0) * 1e3:.1f} ms")
assert jedec == bytes.fromhex(a.id), f"JEDEC ID {jedec.hex()}, want {a.id}"
assert data == bytes(range(16))
if a.status:
    sr0 = cmd(0x13, le24(1) + le24(1) + b"\x05", retlen=1)
    cmd(0x13, le24(1) + le24(0) + b"\x06")
    sr1 = cmd(0x13, le24(1) + le24(1) + b"\x05", retlen=1)
    print("status:", sr0.hex(), "after write enable:", sr1.hex())
    assert sr0 == b"\x00" and sr1 == b"\x02", "status register"
print("PASS")
