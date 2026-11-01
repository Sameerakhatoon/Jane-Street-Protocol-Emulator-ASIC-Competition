"""DPLL: bit timing recovered from the edges of one input pin.

Times are in sixteenths of a cycle. The phase ph runs from 0 to period and
advances 16 per cycle; reaching period starts a new bit (bit_start), and the
strobes q1 (period / 4), half (period / 2) and sample (DPLL_SPT) fire once
per bit. The transmit strobes (bit_start, half) fire in the cycle before the
phase reaches their point, because the codec moves the line at the next
edge; the receive strobes (q1, sample) fire in the first cycle at or past
their point, because they take that cycle's pin value. An enabled edge (rising,
falling or both, DPLL_CTL) either hard-syncs (when armed by CMD.hard_arm:
the bit starts now) or, with resync on, pulls the phase toward the edge by
at most DPLL_SJW, one cycle after the edge. While armed for a hard sync the
q1 and sample strobes are held off, so an idle line is not sampled. Turning
the DPLL off restarts the phase at 0 and forgets which strobes fired, so the
first bit after it is turned on again has all three.

The edge time inside the cycle (lag, in sixteenths since the edge) is the
middle of the cycle, 8, or with the dual-edge input the middle of the half
cycle in which the falling-edge sample s_n saw the change: 12 or 4. Those
constants, and the cycle in which each strobe fires, follow the RTL
interface; the ISA text fixes only the units and the register fields.
"""
from dataclasses import dataclass

M20 = (1 << 20) - 1


@dataclass
class DpllCfg:
    period: int = 0
    spt: int = 0
    sjw: int = 0
    resync: int = 0
    dual: int = 0
    rise: int = 0
    fall: int = 0

    @classmethod
    def from_words(cls, period=0, spt=0, sjw=0, ctl=0):
        return cls(period & M20, spt & M20, sjw & 0xFFFF, ctl & 1, (ctl >> 1) & 1,
                   (ctl >> 2) & 1, (ctl >> 3) & 1)


@dataclass
class DpllIn:
    enable: int = 0
    hard_arm: int = 0
    s_p: int = 0
    s_n: int = 0


class Dpll:
    def __init__(self, cfg=None):
        self.cfg = cfg or DpllCfg()
        self.ph = self.prev = self.armed = self.delta_q = 0
        self.q_done = self.h_done = self.s_done = 0

    def _lag(self, i):
        if not self.cfg.dual:
            return 8
        return 12 if i.s_n != self.prev else 4

    def _edge(self, i):
        c = self.cfg
        rise, fall = not self.prev and i.s_p, self.prev and not i.s_p
        return int(bool(i.enable and (rise and c.rise or fall and c.fall)))

    def _correction(self, lag):
        c = self.cfg
        e = self.ph - lag
        if e < 0:
            e += c.period
        if e < c.period >> 1:
            return -min(e, c.sjw)           # edge after the expected bit start: slow down
        return min(c.period - e, c.sjw)     # edge before it: speed up

    def out(self, i):
        c, en = self.cfg, i.enable
        edge = self._edge(i)
        hard = edge and self.armed
        s = self.ph + 16 + self.delta_q
        wrap = s >= 0 and s >= c.period
        bs = int(bool(en and (wrap or hard)))
        return dict(bit_start=bs,
                    q1=int(bool(en and not self.armed and not self.q_done and self.ph >= c.period >> 2)),
                    half=int(bool(en and not self.h_done and s >= 0 and s >= c.period >> 1)),
                    sample=int(bool(en and not self.armed and not self.s_done and self.ph >= c.spt)),
                    samp_val=i.s_p, edge_seen=edge), (edge, hard, s, wrap)

    def step(self, i):
        o, (edge, hard, s, wrap) = self.out(i)
        lag = self._lag(i)
        self.delta_q = self._correction(lag) if edge and self.cfg.resync and not self.armed else 0
        if not i.enable:
            self.ph = 0
        elif hard:
            self.ph = lag + 16
        elif s < 0:
            self.ph = 0
        elif wrap:
            self.ph = s - self.cfg.period
        else:
            self.ph = s
        bs = o["bit_start"]
        live = i.enable and not bs
        self.q_done = int(bool(live and (self.q_done or o["q1"])))
        self.h_done = int(bool(live and (self.h_done or o["half"])))
        self.s_done = int(bool(live and (self.s_done or o["sample"])))
        if i.hard_arm:
            self.armed = 1
        if hard:
            self.armed = 0
        self.prev = i.s_p
        return o
