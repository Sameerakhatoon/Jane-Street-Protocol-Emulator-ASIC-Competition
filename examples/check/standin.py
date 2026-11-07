# SPDX-License-Identifier: Apache-2.0
"""An outside I2C controller and its target as one scheduled waveform.

Check mode watches a bus the chip does not drive, so the bus here is a
stand-in: the SCL and SDA levels of whole transactions, controller and
target bits together, placed on exact model cycles. Every timing parameter
is a number of cycles, so a test can break one rule by a known amount.

A transaction is a list of tokens in the I2C judge's frame-key form:
    S 50W A 10 A 5A A P        S 50W A 10 A Sr 50R A 7F N P
S and Sr start, P stops, "50W" and "50R" are address bytes, two hex
digits are a data byte, A and N the acknowledge bit.
"""

# Standard-mode limits are 4.0 to 4.7 us (240 to 282 cycles at 60 MHz); the
# clock phases are long because the host pops every edge's record over SPI
# while the bus runs (about 650 cycles a record).
TIMING = dict(t_low=1800, t_high=1800, t_hd_sta=270, t_su_sta=300, t_su_sto=270,
              t_buf=330, t_hd_dat=18)


class Bus:
    def __init__(self, start, **timing):
        self.t = dict(TIMING)
        for k, v in timing.items():
            if k not in self.t:
                raise ValueError("unknown timing parameter %s" % k)
            self.t[k] = v
        self.cycle = start
        self.scl = self.sda = 1
        self.edges = {"scl": [], "sda": []}
        self.marks = []                 # (cycle, what): START, Sr, STOP, SCL edges

    def _set(self, line, level, at):
        if getattr(self, line) != level:
            setattr(self, line, level)
            self.edges[line].append((at, level))
            if line == "scl":
                self.marks.append((at, "SCL_RISE" if level else "SCL_FALL"))

    def _start(self, repeated):
        t = self.t
        if repeated:
            self._set("sda", 1, self.cycle + t["t_hd_dat"])
            self.cycle += t["t_low"]
            self._set("scl", 1, self.cycle)
            self.cycle += t["t_su_sta"]
        self._set("sda", 0, self.cycle)
        self.marks.append((self.cycle, "Sr" if repeated else "START"))
        self.cycle += t["t_hd_sta"]
        self._set("scl", 0, self.cycle)

    def _bit(self, b):
        t = self.t
        self._set("sda", b, self.cycle + t["t_hd_dat"])
        self.cycle += t["t_low"]
        self._set("scl", 1, self.cycle)
        self.cycle += t["t_high"]
        self._set("scl", 0, self.cycle)

    def _stop(self):
        t = self.t
        self._set("sda", 0, self.cycle + t["t_hd_dat"])
        self.cycle += t["t_low"]
        self._set("scl", 1, self.cycle)
        self.cycle += t["t_su_sto"]
        self._set("sda", 1, self.cycle)
        self.marks.append((self.cycle, "STOP"))
        self.cycle += t["t_buf"]

    def transaction(self, key):
        """Add one transaction given as a frame key; returns self."""
        toks = key.split()
        if not toks or toks[0] != "S" or toks[-1] != "P":
            raise ValueError("a transaction runs from S to P: %r" % key)
        for tok in toks:
            if tok in ("S", "Sr"):
                self._start(tok == "Sr")
            elif tok == "P":
                self._stop()
            elif tok in ("A", "N"):
                self._bit(1 if tok == "N" else 0)
            else:
                if len(tok) == 3 and tok[2] in "WR":
                    b = int(tok[:2], 16) << 1 | (tok[2] == "R")
                else:
                    b = int(tok, 16)
                for i in range(7, -1, -1):
                    self._bit((b >> i) & 1)
        return self

    def end(self):
        return self.cycle
