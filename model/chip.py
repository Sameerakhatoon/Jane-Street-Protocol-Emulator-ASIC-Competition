"""The chip as lanes sharing one cycle counter, the chip flags and the data window.

Lane k runs SRAM window k (128 words). SIG writes CF at the end of its cycle,
the highest lane winning a conflict. EMIT records
(cycle, lane, code, x) in issue order; the event unit's FIFO and the
crossbar are not modelled yet.
"""
from .lane import Lane, LaneIn


class Chip:
    def __init__(self, windows, cfgs=None, entries=2, lat=1, tick_log2=None):
        self.dwin = [0] * 128
        self.cycle = 0
        self.cf = 0
        self.tick_log2 = tick_log2
        cfgs = cfgs or [{} for _ in windows]
        self.lanes = [Lane(w, k, cfgs[k], entries, lat, self.dwin, ts=lambda: self.cycle)
                      for k, w in enumerate(windows)]
        self.events, self.irqs = [], []

    def step(self, ins=None):
        ins = ins or [LaneIn() for _ in self.lanes]
        tick = int(self.tick_log2 is not None and self.cycle % (1 << self.tick_log2) == 0)
        chip = self.cf | tick << 5
        cf = self.cf
        for k, (lane, i) in enumerate(zip(self.lanes, ins)):
            i.chip = chip | (i.chip & 0x10)
            o = lane.step(i)
            if o.sig:
                m, v = o.sig
                cf = cf & ~m | v & m
            if o.emit:
                self.events.append((o.emit[2], k, o.emit[0], o.emit[1]))
            if o.irq:
                self.irqs.append((self.cycle, k, o.irq))
        self.cf = cf & 0xF
        self.cycle += 1

    def run(self, n, ins_fn=None):
        for _ in range(n):
            self.step(ins_fn(self.cycle) if ins_fn else None)
        return self
