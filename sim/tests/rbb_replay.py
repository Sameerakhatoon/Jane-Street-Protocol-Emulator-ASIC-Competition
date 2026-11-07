# SPDX-License-Identifier: Apache-2.0
"""Replays OpenOCD remote_bitbang traffic against tse_sim --rbb.

usage: rbb_replay.py PORT jtag|swd

The byte stream is the one OpenOCD's bitbang driver (src/jtag/drivers/bitbang.c)
sends for the same operations: TMS walks as write(0, tms, 0) write(1, tms, 0),
scans as write(0, tms, tdi) R write(1, tms, tdi), SWD bits as d..g pairs with
'c' reads while SWCLK is low and O/o around the turnarounds. Stands in for
`openocd -c "adapter driver remote_bitbang; ..."` until OpenOCD is installed.
"""
import socket
import sys

s = socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=20)
s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
out = []
reads = 0


def w(tck, tms, tdi):
    out.append(chr(ord("0") + tck * 4 + tms * 2 + tdi))


def tms_walk(bits):
    for t in bits:
        w(0, t, 0)
        w(1, t, 0)
    w(0, bits[-1], 0)


def scan(bits):
    """Shift bits (LSB first) ending in Exit1; returns the index of the replies."""
    global reads
    first = reads
    for i, b in enumerate(bits):
        last = 1 if i == len(bits) - 1 else 0
        w(0, last, b)
        out.append("R")
        w(1, last, b)
        reads += 1
    w(0, 1, bits[-1])
    return first


def run():
    s.sendall("".join(out).encode())
    out.clear()
    got = b""
    while len(got) < reads:
        c = s.recv(reads - len(got))
        if not c:
            raise SystemExit("connection closed after %d of %d replies" % (len(got), reads))
        got += c
    return [int(chr(x)) for x in got]


def word(bits):
    return sum(b << i for i, b in enumerate(bits))


def jtag():
    tms_walk([1, 1, 1, 1, 1, 0])                    # Test-Logic-Reset, Run-Test/Idle
    tms_walk([1, 0, 0])                             # to Shift-DR
    a = scan([0] * 32)
    tms_walk([1, 0])                                # Update-DR, Run-Test/Idle
    tms_walk([1, 1, 0, 0])                          # to Shift-IR
    b = scan([1, 1, 1, 1])                          # BYPASS
    tms_walk([1, 0])
    tms_walk([1, 0, 0])
    pat = [1, 0, 1, 0, 0, 1, 0, 1]
    c = scan(pat)
    tms_walk([1, 0])
    r = run()
    idcode = word(r[a:a + 32])
    ircap = word(r[b:b + 4])
    byp = r[c:c + 8]
    print("IDCODE 0x%08X, IR capture %s, bypass %s" % (idcode, format(ircap, "04b"), "".join(map(str, byp))))
    assert idcode == 0x15E00001, "IDCODE"
    assert ircap & 3 == 1, "IR capture must end in 01"
    assert byp == [0] + pat[:-1], "bypass is a one-bit delay"


def swd_bits(bits):
    for b in bits:
        out.append(chr(ord("d") + b))
        out.append(chr(ord("d") + 2 + b))


def swd_read_bits(n):
    global reads
    first = reads
    for _ in range(n):
        out.append("d")
        out.append("c")
        out.append("f")
        reads += 1
    return first


def swd_cmd(ap, rnw, addr):
    a2, a3 = (addr >> 2) & 1, (addr >> 3) & 1
    return [1, ap, rnw, a2, a3, ap ^ rnw ^ a2 ^ a3, 0, 1]


def swd_read(addr):
    out.append("O")
    swd_bits(swd_cmd(0, 1, addr))
    out.append("o")
    k = swd_read_bits(1 + 3 + 32 + 1 + 1)
    out.append("O")
    swd_bits([0] * 8)
    return k


def swd_write(addr, val):
    out.append("O")
    swd_bits(swd_cmd(0, 0, addr))
    out.append("o")
    k = swd_read_bits(5)
    out.append("O")
    data = [(val >> i) & 1 for i in range(32)]
    swd_bits(data + [sum(data) & 1])
    swd_bits([0] * 8)
    return k


def swd():
    out.append("O")
    swd_bits([1] * 51)                              # line reset
    swd_bits([(0xE79E >> i) & 1 for i in range(16)])   # JTAG-to-SWD
    swd_bits([1] * 51 + [0] * 2)
    k1 = swd_read(0x0)                              # DPIDR
    k2 = swd_write(0x8, 0x00F0_00A5)                # SELECT
    k3 = swd_write(0x4, 0x5000_0000)                # CTRL/STAT power-up requests
    k4 = swd_read(0x4)
    k5 = swd_read(0xC)                              # RDBUFF
    r = run()

    def rd(k):
        ack = r[k + 1:k + 4]
        data = r[k + 4:k + 36]
        assert ack == [1, 0, 0], "ack %s" % ack
        assert r[k + 36] == sum(data) & 1, "read parity"
        return word(data)

    for k in (k2, k3):
        assert r[k + 1:k + 4] == [1, 0, 0], "write ack %s" % r[k + 1:k + 4]
    dpidr, ctrl, rdbuf = rd(k1), rd(k4), rd(k5)
    print("DPIDR 0x%08X, CTRL/STAT 0x%08X, RDBUFF 0x%08X" % (dpidr, ctrl, rdbuf))
    assert dpidr == 0x0BC11477, "DPIDR"
    assert ctrl == 0xF0000000, "power-up acknowledged"
    assert rdbuf == 0x00F000A5, "SELECT written"


{"jtag": jtag, "swd": swd}[sys.argv[2]]()
s.sendall(b"Q")
print("PASS")
