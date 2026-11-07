# SPDX-License-Identifier: Apache-2.0
"""cocotb: the stream generator against the chip's loader (src/tse_loader.sv inside the
whole chip, driven through the pins with PinLink's timing).

These were expected failures against the earlier loader (0xA5 packets, one byte per
clock, CRC-16, no STAT); the chip now speaks the protocol this library sends, so all
of them must pass.
"""
import binascii

import cocotb

from tse_host import frames as F
from chip_pins import Pins

WORDS = [0x11223344, 0xA5A5A5A5, 0x00000001, 0xDEADBEEF]
LOADED = 0x01


async def bench(dut, hold=4):
    p = Pins(dut, hold=hold, read_wait=min(hold, 10) if isinstance(hold, int) else 4)
    await p.start()
    await p.enter_load()
    return p


async def load(p, words=WORDS):
    stream, crc = F.load_stream([(0, words)])
    await p.exchange(stream)
    return crc


@cocotb.test()
async def load_start_clears(dut):
    """Entering load mode clears loaded, after a load that set it."""
    p = await bench(dut)
    await load(p)
    assert await p.stat() & LOADED
    await p.leave_load()
    await p.enter_load()
    assert await p.stat() & LOADED == 0


@cocotb.test()
async def old_packets_do_nothing(dut):
    """The earlier 0xA5 packet format, sent with frame bits, writes nothing and loads nothing."""
    p = await bench(dut)
    await p.exchange(F.begin() + F.wrb(0, [0] * 4))
    data = b"".join(F.be32(w) for w in WORDS)
    await p.exchange([(b, 0) for b in bytes([0xA5, 0x01, 0, 0, 3]) + data] + [(0xA5, 1), (0x06, 0)])
    assert await p.stat() & LOADED == 0
    assert await p.read(0, 4) == [0] * 4


@cocotb.test()
async def readback_msb_first(dut):
    """Words come back most significant byte first."""
    p = await bench(dut)
    await load(p)
    seen = await p.exchange(F.rd(0))
    assert bytes(seen[-4:]) == bytes([0x11, 0x22, 0x33, 0x44])


@cocotb.test()
async def current_stream_loads(dut):
    """BEGIN, WRB and COMMIT from the stream generator write the SRAM and set loaded."""
    p = await bench(dut)
    await load(p)
    assert await p.stat() & LOADED
    assert await p.read(0, 4) == WORDS


@cocotb.test()
async def held_bytes_are_one_byte(dut):
    """A byte held for 8 clocks counts once per strobe edge."""
    p = await bench(dut, hold=8)
    await load(p)
    assert await p.read(0, 4) == WORDS


@cocotb.test()
async def commit_checks_crc32(dut):
    """COMMIT compares CRC-32/ISO-HDLC over address and data bytes: a CRC-16 is refused,
    the CRC-32 loads."""
    p = await bench(dut)
    body, crc = F.load_body([(0, WORDS)])
    crc16 = binascii.crc_hqx(b"".join(F.be32(w) for w in WORDS), 0xFFFF)
    await p.exchange(F.begin() + body + F.commit(crc16))
    assert await p.stat() & LOADED == 0
    await p.exchange(F.commit(crc))
    assert await p.stat() & LOADED


@cocotb.test()
async def stat_presents_status(dut):
    """STAT plus one strobe shows STATUS[7:0]: loaded, BIST done, BIST pass, no host error."""
    p = await bench(dut)
    await p.exchange(F.prologue(with_bist=True))
    await p.tick(12000)
    body, crc = F.load_body([(0, WORDS)])
    await p.exchange(body + F.commit(crc))
    assert await p.stat() & 0x0F == 0x07
