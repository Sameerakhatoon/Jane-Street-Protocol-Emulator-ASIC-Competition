"""Links: how a session reaches the chip pins.

A link has five operations, and the session needs nothing else:
    enter_load()          rst_n high for at least 4 clocks, then low
    leave_load()          rst_n high (run mode)
    exchange(stream)      send (byte, frame) pairs on the byte bus and return
                          uo_out as seen after each strobe
    wait(cycles)          let chip clocks pass (the self-test)
    spi(mosi, nread)      one runtime SPI transaction, returns nread bytes
PinLink implements them on any pin bus: an object with
set_inputs(ui_in=, uio_in=, rst_n=), tick(n) and a uo_out attribute. The
Python chip model is one; a Verilated chip behind a harness is another.
The board implements the same five with PIO and hardware SPI.

Portable: CPython 3 and MicroPython.
"""
RESET_HIGH = 8          # clocks of rst_n high before load mode (the chip needs 4)
SETTLE = 4              # clocks after an rst_n change for the synchronizer


class PinLink:
    def __init__(self, pins, hold=4, read_wait=10, spi_half=8, rng=None):
        """hold: clocks per byte (an int, or (lo, hi) for random spacing with rng);
        read_wait: clocks from a strobe edge to sampling uo_out (at most hold);
        spi_half: clocks per SCK half period."""
        self.pins = pins
        self.hold = hold
        self.read_wait = read_wait
        self.spi_half = spi_half
        self.rng = rng
        self.strobe = 0
        self.uio = 0

    def enter_load(self):
        self.pins.set_inputs(rst_n=1)
        self.pins.tick(RESET_HIGH)
        self.pins.set_inputs(rst_n=0)
        self.pins.tick(SETTLE)

    def leave_load(self):
        self.uio = 0
        self.strobe = 0                 # the pin goes low, so the next load starts from 0
        self.pins.set_inputs(ui_in=0x01, uio_in=0, rst_n=1)     # CS_N idle high
        self.pins.tick(SETTLE)

    def _gap(self):
        h = self.hold
        if isinstance(h, tuple):
            return self.rng.randint(h[0], h[1])
        return h

    def exchange(self, stream):
        seen = []
        for byte, frame in stream:
            self.strobe ^= 1
            self.uio = self.strobe | (frame << 1)
            self.pins.set_inputs(ui_in=byte, uio_in=self.uio)
            gap = max(self._gap(), self.read_wait)
            self.pins.tick(self.read_wait)
            seen.append(self.pins.uo_out)
            self.pins.tick(gap - self.read_wait)
        return seen

    def wait(self, cycles):
        self.pins.tick(cycles)

    def spi(self, mosi, nread):
        """Mode 0: MOSI set while SCK is low, both sides sample on the rising edge."""
        half = self.spi_half
        p = self.pins
        p.set_inputs(ui_in=0x00)                   # CS_N low, SCK low
        p.tick(half)
        out = bytes(mosi) + bytes(nread)
        reply = bytearray()
        for k, byte in enumerate(out):
            acc = 0
            for i in range(7, -1, -1):
                d = (byte >> i) & 1
                p.set_inputs(ui_in=d << 2)
                p.tick(half)
                acc = (acc << 1) | ((p.uo_out >> 3) & 1)
                p.set_inputs(ui_in=0x02 | d << 2)
                p.tick(half)
            if k >= len(mosi):
                reply.append(acc)
        p.set_inputs(ui_in=0x00)
        p.tick(half)
        p.set_inputs(ui_in=0x01)                   # CS_N high ends the transaction
        p.tick(half)
        return bytes(reply)
