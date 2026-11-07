# SPDX-License-Identifier: Apache-2.0
"""Pin drivers for the chip under cocotb, with the host library's timing.

The byte bus is driven exactly as host/tse_host/link.py's PinLink drives it
(data, frame and the strobe toggle change together, uo_out sampled
read_wait clocks later), so the RTL meets the same streams the host sends.
SPI is mode 0 with spi_half clocks per SCK half period. Inputs change just
after a rising clock edge, as the model's tick() boundary.
"""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

from tse_host import frames as F

RESET_HIGH = 8
SETTLE = 4


class Pins:
    def __init__(self, dut, hold=4, read_wait=4, spi_half=8, seed=1):
        self.dut = dut
        self.hold = hold
        self.read_wait = read_wait
        self.spi_half = spi_half
        self.rng = random.Random(seed)
        self.strobe = 0

    async def start(self):
        d = self.dut
        cocotb.start_soon(Clock(d.clk, 16666, unit="ps").start())
        d.ena.value = 1
        d.ui_in.value = 0
        d.uio_in.value = 0
        d.rst_n.value = 1
        await ClockCycles(d.clk, 2)

    async def tick(self, n):
        if n > 0:
            await ClockCycles(self.dut.clk, n)

    @property
    def uo_out(self):
        return int(self.dut.uo_out.value)

    def set(self, ui=None, uio=None, rst=None):
        if ui is not None:
            self.dut.ui_in.value = ui
        if uio is not None:
            self.dut.uio_in.value = uio
        if rst is not None:
            self.dut.rst_n.value = rst

    async def enter_load(self, settle=SETTLE):
        self.set(rst=1)
        await self.tick(RESET_HIGH)
        self.set(rst=0)
        await self.tick(settle)

    async def leave_load(self):
        self.set(ui=0x01, uio=0, rst=1)              # CS_N idle high
        await self.tick(SETTLE)

    def _gap(self):
        h = self.hold
        return self.rng.randint(h[0], h[1]) if isinstance(h, tuple) else h

    async def exchange(self, stream):
        """Send (byte, frame) pairs; return uo_out as seen after each strobe."""
        seen = []
        for byte, frame in stream:
            self.strobe ^= 1
            self.set(ui=byte, uio=self.strobe | (frame << 1))
            gap = max(self._gap(), self.read_wait)
            await self.tick(self.read_wait)
            seen.append(self.uo_out)
            await self.tick(gap - self.read_wait)
        return seen

    async def stat(self):
        return (await self.exchange(F.stat()))[-1]

    async def read(self, addr, count=1):
        seen = await self.exchange(F.rdb(addr, count))
        return F.words_from(seen, 5, count)

    async def spi(self, mosi, nread):
        half = self.spi_half
        self.set(ui=0x00)
        await self.tick(half)
        reply = bytearray()
        out = bytes(mosi) + bytes(nread)
        for k, byte in enumerate(out):
            acc = 0
            for i in range(7, -1, -1):
                d = (byte >> i) & 1
                self.set(ui=d << 2)
                await self.tick(half)
                acc = (acc << 1) | ((self.uo_out >> 3) & 1)
                self.set(ui=0x02 | d << 2)
                await self.tick(half)
            if k >= len(mosi):
                reply.append(acc)
        self.set(ui=0x00)
        await self.tick(half)
        self.set(ui=0x01)
        await self.tick(half)
        return bytes(reply)

    async def spi_write(self, addr, value):
        await self.spi(bytes([F.WR]) + F.be16(addr) + F.be32(value), 0)

    async def spi_read(self, addr, count=1):
        if count == 1:
            r = await self.spi(bytes([F.RD]) + F.be16(addr) + b"\x00", 4)
        else:
            r = await self.spi(bytes([F.RDB]) + F.be16(addr) + F.be16(count) + b"\x00", 4 * count)
        return F.words_from(list(r), 0, count)

    async def spi_stat(self):
        return (await self.spi(bytes([F.STAT]), 1))[0]
