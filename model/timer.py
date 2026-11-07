# SPDX-License-Identifier: Apache-2.0
"""Lane timer: 24-bit NCO tick plus a 16-bit down counter, and the sticky TIMER flag.

TIMER_INC [23:0] inc; TIMER_CTL reload [15:0], on_tick [16], en [17],
ev0_sel [18]. The counter loads reload on CMD.frame_rst and counts NCO
ticks (on_tick) or cycles; reaching zero pulses
expired one cycle later, which sets the sticky flag one cycle after that.
The flag clears on frame_rst and when a WAIT 1, EV, 0 it satisfied ends.
Cycle placement of tick, expired and flag follows the RTL (the ISA text does
not fix it).
"""


class Timer:
    def __init__(self, inc=0, ctl=0, auto=False):
        self.inc, self.ctl, self.auto = inc & 0xFFFFFF, ctl, auto
        self.acc = self.tick = self.count = self.expired = self.flag = 0

    @property
    def en(self):
        return (self.ctl >> 17) & 1

    @property
    def ev0_sel(self):
        return (self.ctl >> 18) & 1

    def step(self, frame_rst=0, ack=0):
        reload, on_tick = self.ctl & 0xFFFF, (self.ctl >> 16) & 1
        stepping = self.en and (self.tick if on_tick else 1)
        expired = self.expired
        if frame_rst or ack:
            self.flag = 0
        elif expired:
            self.flag = 1
        self.expired = 0
        if frame_rst:
            self.count = reload
        elif stepping and self.count:
            if self.count == 1:
                self.expired = 1
            self.count = reload if self.count == 1 and self.auto else self.count - 1
        if self.en:
            s = self.acc + self.inc
            self.acc, self.tick = s & 0xFFFFFF, s >> 24
        else:
            self.acc = self.tick = 0
