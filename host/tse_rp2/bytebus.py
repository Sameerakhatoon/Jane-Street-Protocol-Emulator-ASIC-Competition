# SPDX-License-Identifier: Apache-2.0
"""PIO programs for the load-mode byte bus (MicroPython on the RP2350B).

One 16-bit entry per byte, two entries per TX FIFO word, low half first:
    bits 7:0 -> ui_in[7:0] = GPIO17..24 (data)
    bit 8    -> uio[0]     = GPIO25 (strobe)
    bit 9    -> uio[1]     = GPIO26 (frame)
    bits 15:10 discarded
The host flips bit 8 in every new entry, so every entry is exactly one strobe
edge; repeating the previous entry unchanged (padding) makes no edge at all.
A dry FIFO stalls `out` with the pins unchanged, so a slow Python loop can
never make a spurious strobe.

The PIO block must see GPIO16..47 (PIO(0).gpio_base(Pin(16))) to reach both
the load pins and uo_out (GPIO33..40).
"""
import rp2

OUT_BASE = 17           # ui_in[0]
IN_BASE = 33            # uo_out[0]
OUT_PINS = 10
TX_CYCLES = 9           # PIO cycles per written byte
RX_CYCLES = 33          # PIO cycles per read byte
RX_SAMPLE = 32          # PIO cycles from the strobe edge to sampling uo_out


# Write path: at a 30 MHz PIO clock one entry takes 9 cycles = 300 ns =
# 18 chip clocks at 60 MHz, against the loader's minimum of 4.
@rp2.asm_pio(out_init=(rp2.PIO.OUT_LOW,) * 10, out_shiftdir=rp2.PIO.SHIFT_RIGHT,
             autopull=True, pull_thresh=32)
def loader_tx():
    out(pins, 10)       [7]
    out(null, 6)


# Read path: the same entries; 32 PIO cycles (about 1.07 us) after each
# strobe edge it samples uo_out, far beyond the loader's read latency plus
# the pad delay. Four samples make one RX word, first byte in bits 31:24.
@rp2.asm_pio(out_init=(rp2.PIO.OUT_LOW,) * 10, out_shiftdir=rp2.PIO.SHIFT_RIGHT,
             autopull=True, pull_thresh=32,
             in_shiftdir=rp2.PIO.SHIFT_LEFT, autopush=True, push_thresh=32)
def loader_rx():
    out(pins, 10)       [15]
    out(null, 6)        [15]
    in_(pins, 8)


class Entries:
    """Turns (byte, frame) streams into FIFO words, keeping the strobe level
    across calls and across both programs."""

    def __init__(self):
        self.strobe = 0
        self.last = 0

    def entries(self, stream, multiple):
        out = []
        for byte, frame in stream:
            self.strobe ^= 1
            self.last = (byte & 0xFF) | (self.strobe << 8) | ((frame & 1) << 9)
            out.append(self.last)
        while len(out) % multiple:
            out.append(self.last)
        return out

    def words(self, stream, multiple=2):
        """FIFO words for a stream, padded to a multiple of entries."""
        e = self.entries(stream, multiple)
        return [e[i] | (e[i + 1] << 16) for i in range(0, len(e), 2)]


def rx_bytes(words, n):
    """The first n uo_out samples from RX FIFO words (first sample in bits 31:24)."""
    out = bytearray()
    for w in words:
        out += bytes(((w >> 24) & 0xFF, (w >> 16) & 0xFF, (w >> 8) & 0xFF, w & 0xFF))
    return bytes(out[:n])


def pio_freq(sys_hz, target=30000000):
    """Highest PIO clock at or below target with an integer divider (no jitter)."""
    div = (sys_hz + target - 1) // target
    return sys_hz // div
