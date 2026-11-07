# SPDX-License-Identifier: Apache-2.0
"""CPython stand-in for MicroPython's machine module (Pin, SPI, freq), wired to rp2.WORLD."""
from rp2 import WORLD, SimError


def freq():
    return WORLD.sys_hz


class Pin:
    IN, OUT = 0, 1

    def __init__(self, n, mode=None, value=None):
        self.id = n
        self.mode = mode
        if mode == Pin.OUT:
            WORLD.owner[n] = "cpu"
            WORLD.cpu_out[n] = 1 if value else 0
            WORLD.dirty = True
        elif mode == Pin.IN:
            WORLD.owner[n] = "in"
            WORLD.dirty = True

    def __call__(self, v=None):
        if v is None:
            return WORLD.level(self.id)
        WORLD.cpu_out[self.id] = 1 if v else 0
        WORLD.dirty = True
        WORLD.run_us(1)                 # a MicroPython pin call takes about a microsecond

    def value(self, v=None):
        return self(v)


class SPI:
    MSB = 0

    def __init__(self, n, baudrate, polarity=0, phase=0, bits=8, firstbit=0, sck=None, mosi=None,
                 miso=None):
        if (n, polarity, phase, bits, firstbit) != (0, 0, 0, 8, SPI.MSB):
            raise SimError("only SPI0 mode 0, 8 bits, MSB first is simulated")
        self.sck, self.mosi, self.miso = sck.id, mosi.id, miso.id
        self.half = WORLD.sys_hz // (2 * baudrate)
        for p in (self.sck, self.mosi):
            WORLD.owner[p] = "spi"
            WORLD.spi_out[p] = 0
        WORLD.dirty = True

    def _byte(self, b):
        got = 0
        for i in range(7, -1, -1):
            WORLD.spi_out[self.mosi] = (b >> i) & 1
            WORLD.spi_out[self.sck] = 0
            WORLD.dirty = True
            WORLD.run(self.half)
            got = (got << 1) | WORLD.level(self.miso)
            WORLD.spi_out[self.sck] = 1
            WORLD.dirty = True
            WORLD.run(self.half)
        WORLD.spi_out[self.sck] = 0
        WORLD.dirty = True
        return got

    def write(self, buf):
        for b in buf:
            self._byte(b)

    def read(self, n, write=0):
        return bytes(self._byte(write) for _ in range(n))
