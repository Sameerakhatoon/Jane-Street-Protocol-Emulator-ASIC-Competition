# SPDX-License-Identifier: Apache-2.0
"""Line codec: NRZ, NRZI and Manchester, encoder and decoder.

CODEC_CFG: mode [1:0] (0 NRZ, 1 NRZI, 2 Manchester), inv [2] (NRZ polarity,
or Manchester convention: 0 IEEE, a 1 is low then high; 1 Thomas), t_on [3]
(NRZI toggles on a 1; 0 toggles on a 0, as USB). line_set forces the line
(idle levels) and wins over a step. The encoder changes the line at the end
of a tx_step cycle (Manchester: first half) and of a tx_half cycle (second
half). The decoder registers its bit at rx_sample, valid the cycle after;
Manchester compares the two halves (q1 strobe and sample) and flags equal
halves as a code violation. Mode 3 behaves as NRZ.
"""
from dataclasses import dataclass

NRZ, NRZI, MAN = 0, 1, 2


@dataclass
class CodecIn:
    tx_step: int = 0
    tx_half: int = 0
    tx_bit: int = 0
    line_set: int = 0
    line_val: int = 0
    rx_sample: int = 0
    rx_s: int = 0
    rx_q1: int = 0
    rx_q1_s: int = 0


class Codec:
    def __init__(self, mode=0, inv=0, t_on=0):
        self.mode, self.inv, self.t_on = mode, inv, t_on
        self.line = self.mbit = 0
        self.rprev = self.h1 = 0
        self.rx_valid = self.rx_bit = self.rx_err = 0

    def out(self):
        return dict(tx_line=self.line, rx_valid=self.rx_valid, rx_bit=self.rx_bit,
                    rx_err=self.rx_err)

    def step(self, i):
        o = self.out()
        if i.line_set:
            self.line = i.line_val
        elif i.tx_step:
            self.mbit = i.tx_bit
            if self.mode == NRZI:
                self.line ^= int(i.tx_bit == self.t_on)
            elif self.mode == MAN:
                self.line = 1 - i.tx_bit ^ self.inv
            else:
                self.line = i.tx_bit ^ self.inv
        elif i.tx_half and self.mode == MAN:
            self.line = self.mbit ^ self.inv
        self.rx_valid, self.rx_err = i.rx_sample, 0
        h1 = self.h1
        if i.rx_q1:
            self.h1 = i.rx_q1_s
        if i.rx_sample:
            if self.mode == NRZI:
                self.rx_bit = self.t_on if i.rx_s != self.rprev else 1 - self.t_on
            elif self.mode == MAN:
                self.rx_bit, self.rx_err = i.rx_s ^ self.inv, int(h1 == i.rx_s)
            else:
                self.rx_bit = i.rx_s ^ self.inv
            self.rprev = i.rx_s
        return o
