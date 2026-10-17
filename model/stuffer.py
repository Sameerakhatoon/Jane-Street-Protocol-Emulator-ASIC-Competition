"""Bit stuffer (TX) and destuffer (RX).

STUFF_CFG: n [3:0] (0 disables), count runs of ones [4], of zeros [5]. After
n equal bits of a counted polarity, TX inserts the complement and RX drops
the next bit, flagging a stuff error if it is not the complement. The stuff
bit is the first bit of the next run (CAN and USB both count it so).
Cycle placement follows the RTL interface: TX is combinational on tx_step
(the bit sent this step is tx_dout, tx_take says a data bit was consumed);
RX results are registered, valid in the cycle after rx_step.
"""
from dataclasses import dataclass


@dataclass
class StuffIn:
    clear: int = 0
    tx_step: int = 0
    tx_din: int = 0
    rx_step: int = 0
    rx_din: int = 0


class Stuffer:
    def __init__(self, n=0, mode=0):
        self.n, self.mode = n, mode
        self.trun = self.tlast = 0
        self.rrun = self.rlast = 0
        self.rx_valid = self.rx_dout = self.rx_err = 0

    def _due(self, run, last):
        return (self.n != 0 and run == self.n
                and bool(self.mode & 1 and last or self.mode & 2 and not last))

    def out(self, i):
        pend = self._due(self.trun, self.tlast)
        dout = 1 - self.tlast if pend else i.tx_din
        return dict(tx_pending=int(pend), tx_dout=dout, tx_take=int(i.tx_step and not pend),
                    rx_valid=self.rx_valid, rx_dout=self.rx_dout, rx_err=self.rx_err)

    def step(self, i):
        o = self.out(i)
        if i.clear:
            self.trun = self.tlast = 0
        elif i.tx_step:
            d = o["tx_dout"]
            self.trun = min(self.trun + 1, 15) if d == self.tlast else 1
            self.tlast = d
        if i.clear:
            self.rrun = self.rlast = self.rx_valid = self.rx_err = 0
        elif i.rx_step:
            stuff = self._due(self.rrun, self.rlast)
            if stuff:
                self.rx_valid, self.rx_err, self.rrun = 0, int(i.rx_din == self.rlast), 1
            else:
                self.rx_valid, self.rx_dout, self.rx_err = 1, i.rx_din, 0
                self.rrun = min(self.rrun + 1, 15) if i.rx_din == self.rlast else 1
            self.rlast = i.rx_din
        else:
            self.rx_valid = self.rx_err = 0
        return o


def stuff(bits, n, mode):
    """Line bits for a data bit stream (pure function for tests)."""
    s, out, k = Stuffer(n, mode), [], 0
    while k < len(bits):
        o = s.step(StuffIn(tx_step=1, tx_din=bits[k]))
        out.append(o["tx_dout"])
        k += o["tx_take"]
    return out


def destuff(line, n, mode):
    """(data bits, error count) recovered from line bits."""
    s, out, errs = Stuffer(n, mode), [], 0
    for b in line + [0]:
        o = s.step(StuffIn(rx_step=1, rx_din=b))
        if o["rx_valid"]:
            out.append(o["rx_dout"])
        errs += o["rx_err"]
    return out, errs
