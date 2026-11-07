# SPDX-License-Identifier: Apache-2.0
"""Runtime register access over the RP2350's hardware SPI0 (MicroPython).

CS_N = GPIO17 (ui_in[0]), SCK = GPIO18 (ui_in[1]), MOSI = GPIO19 (ui_in[2]),
MISO = GPIO36 (uo_out[3]). Transactions come from tse_host.spi, the same
encoder the simulation uses. Create TseRegs after the project clock is set:
changing the RP2350 system clock re-times SPI.
"""
from machine import SPI, Pin

from tse_host import spi, regs_map

CS_PIN, SCK_PIN, MOSI_PIN, MISO_PIN = 17, 18, 19, 36
BAUD = 4000000          # reads need MISO back within half an SCK period


class TseRegs:
    def __init__(self, baud=BAUD):
        self.cs = Pin(CS_PIN, Pin.OUT, value=1)
        self.bus = SPI(0, baudrate=baud, polarity=0, phase=0, bits=8, firstbit=SPI.MSB,
                       sck=Pin(SCK_PIN), mosi=Pin(MOSI_PIN), miso=Pin(MISO_PIN))

    def xfer(self, mosi, nread):
        self.cs(0)
        try:
            self.bus.write(mosi)
            return self.bus.read(nread) if nread else b""
        finally:
            self.cs(1)

    def write32(self, addr, value):
        self.xfer(*spi.write(addr, value))

    def read32(self, addr):
        return spi.words(self.xfer(*spi.read(addr)))[0]

    def write_block(self, addr, words):
        self.xfer(*spi.write_block(addr, words))

    def read_block(self, addr, n):
        return spi.words(self.xfer(*spi.read_block(addr, n)))

    def status(self):
        return spi.decode_reg(regs_map.CHIP["STATUS"], self.read32(regs_map.STATUS))

    def events(self, epoch=0):
        """Drain the event FIFO: ([(ts32, lane, code, data)], epoch)."""
        recs = []
        while True:
            n = self.read32(regs_map.EV_STATUS) & 0xF
            if not n:
                break
            recs += [self.read32(regs_map.EV_POP) for _ in range(n)]
        return spi.timeline(recs, epoch)
