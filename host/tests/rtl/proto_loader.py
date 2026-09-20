"""cocotb: the stream generator against the earlier full-chip loader (tse_loader.sv, a copy).

That loader predates the current protocol: it samples ui_in once per clock
(no strobe, no frame bit), starts packets with 0xA5, checks CRC-16/CCITT
over data bytes only, uses its own command codes and a run key in a config
word. Tests marked expect_fail show each place the current host side and
that RTL disagree; the others show what still matches.
"""
import binascii

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

from tse_host import frames as F

KEY = 0x7E5A
SYNC, P_WRITE, P_READ, P_CHECK, P_CLEAR, P_COMMIT = 0xA5, 1, 2, 3, 4, 6


class Bench:
    def __init__(self, dut):
        self.dut = dut
        self.sram = {}
        self.banks = {}
        self.clears = 0

    async def start(self):
        d = self.dut
        cocotb.start_soon(Clock(d.clk, 16666, unit="ps").start())
        d.rst_n.value = 0
        d.din.value = 0
        d.p_rdata.value = 0
        d.c_rdata.value = 0
        d.st_busy.value = 0
        d.st_done.value = 1
        d.st_pass.value = 1
        d.st_info.value = 0
        d.key_ok.value = 0
        cocotb.start_soon(self.memories())
        await ClockCycles(d.clk, 3)
        d.rst_n.value = 1                    # loader leaves reset: load mode starts
        await ClockCycles(d.clk, 2)

    async def memories(self):
        d = self.dut
        while True:
            await FallingEdge(d.clk)
            if int(d.c_clr.value):
                self.banks.clear()
                self.clears += 1
            if int(d.p_req.value):
                a = int(d.p_addr.value)
                if int(d.p_we.value):
                    self.sram[a] = int(d.p_wdata.value)
                d.p_rdata.value = self.sram.get(a, 0)
            if int(d.c_we.value):
                self.banks[(int(d.c_bank.value), int(d.c_word.value))] = int(d.c_wdata.value)
            d.c_rdata.value = self.banks.get((int(d.c_bank.value), int(d.c_word.value)), 0)
            d.key_ok.value = int(self.banks.get((15, 31), 0) == KEY)

    async def bytes_per_clock(self, data, hold=1):
        """The old bus: a new byte every `hold` clocks; returns dout after each clock."""
        seen = []
        for b in data:
            for _ in range(hold):
                await FallingEdge(self.dut.clk)
                self.dut.din.value = b
                seen.append(int(self.dut.dout.value))
        for _ in range(8):
            await FallingEdge(self.dut.clk)
            self.dut.din.value = 0
            seen.append(int(self.dut.dout.value))
        return seen

    async def strobed(self, stream, hold=4):
        """The current bus, as far as this RTL has pins for it: data held for `hold` clocks
        per strobe edge; it has no strobe or frame input, so those are dropped."""
        return await self.bytes_per_clock([b for b, _ in stream], hold)

    def loaded(self):
        return self.banks.get((15, 31), 0) == KEY


def proto_write(addr, words):
    data = b"".join(F.be32(w) for w in words)
    return bytes([SYNC, P_WRITE, addr >> 8, addr & 0xFF, len(words) - 1]) + data + bytes(4), data


def proto_load(words, crc16=True):
    pkt, data = proto_write(0, words)
    crc = binascii.crc_hqx(data, 0xFFFF) if crc16 else F.crc32(F.be16(0) + data) & 0xFFFF
    return (bytes([SYNC, P_CLEAR]) + bytes(3) + pkt
            + bytes([SYNC, P_CHECK, crc >> 8, crc & 0xFF]) + bytes(3) + bytes([SYNC, P_COMMIT]))


WORDS = [0x11223344, 0xA5A5A5A5, 0x00000001, 0xDEADBEEF]


@cocotb.test()
async def load_start_clears(dut):
    """Matches: entering load mode clears the configuration (and so the loaded key) before
    any command arrives."""
    b = Bench(dut)
    b.banks[(15, 31)] = KEY                  # woke up looking loaded
    await b.start()
    assert b.clears == 1 and not b.loaded()


@cocotb.test()
async def own_protocol_loads(dut):
    """Bench check: the RTL's own packet format writes the SRAM and sets the key."""
    b = Bench(dut)
    await b.start()
    await b.bytes_per_clock(proto_load(WORDS))
    assert [b.sram.get(i) for i in range(4)] == WORDS and b.loaded()


@cocotb.test()
async def readback_msb_first(dut):
    """Matches: words come back most significant byte first."""
    b = Bench(dut)
    await b.start()
    await b.bytes_per_clock(proto_load(WORDS))
    seen = await b.bytes_per_clock(bytes([SYNC, P_READ, 0, 0, 0]) + bytes(8))
    assert bytes([0x11, 0x22, 0x33, 0x44]) in bytes(seen)


@cocotb.test(expect_fail=True)
async def current_stream_loads(dut):
    """Diverges: BEGIN, WRB and COMMIT from the stream generator are ignored, because the
    RTL waits for a 0xA5 sync byte and has no frame bit marking command bytes."""
    b = Bench(dut)
    await b.start()
    stream, crc = F.load_stream([(0, WORDS)])
    await b.strobed(stream)
    assert [b.sram.get(i) for i in range(4)] == WORDS and b.loaded()


@cocotb.test(expect_fail=True)
async def held_bytes_are_one_byte(dut):
    """Diverges: on the current bus a byte stays on ui_in for 4 or more clocks and counts
    once per strobe edge; this RTL reads ui_in every clock, so a held byte counts 4 times."""
    b = Bench(dut)
    await b.start()
    pkt = proto_load(WORDS)
    await b.bytes_per_clock(pkt, hold=4)
    assert [b.sram.get(i) for i in range(4)] == WORDS


@cocotb.test(expect_fail=True)
async def commit_checks_crc32(dut):
    """Diverges: COMMIT must compare CRC-32/ISO-HDLC over address and data bytes; this RTL
    checks CRC-16/CCITT over data bytes only, so a CRC-32 based check never loads."""
    b = Bench(dut)
    await b.start()
    await b.bytes_per_clock(proto_load(WORDS, crc16=False))
    assert b.loaded()


@cocotb.test(expect_fail=True)
async def stat_presents_status(dut):
    """Diverges: STAT (0x13) plus one strobe must show STATUS[7:0] (loaded in bit 0); this RTL
    has no STAT and shows {key, crc_ok, done, pass, busy, 101} on idle dout."""
    b = Bench(dut)
    await b.start()
    await b.bytes_per_clock(proto_load(WORDS))
    seen = await b.strobed(F.stat())
    assert seen[-1] & 0x0F == 0x07           # loaded, bist done, bist pass, no host error
