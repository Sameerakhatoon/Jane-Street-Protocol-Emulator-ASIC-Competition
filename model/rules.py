# SPDX-License-Identifier: Apache-2.0
"""Response rules 0 and 1 of one lane.

Both rules share a 16-bit history and an 8-bit frame bit counter that
saturates at 255. RULE_CLK.src selects bit mode (recovered bits, paced by
BITSTART) or pin-edge mode (counted edges of clk_pin, bit = data pin one
cycle before the edge, every drive change tick_delay cycles after its edge).
With RULE_CLK.hold the drive keeps its last bit after the response until
the program writes the driven pin (seq_wr) and ends at that clock edge;
a new match in that cycle wins.
drv_en / drv_val are the drive register: a change made in cycle n is visible
in n + 1 and reaches the port one edge later. Where the ISA text is
silent (a match while a response is pending, a bit in the frame_rst cycle)
the model follows the RTL.
"""
from dataclasses import dataclass


@dataclass
class Rule:
    match: int = 0         # RULEr_MATCH: pattern [15:0], mask [31:16]
    resp_pos: int = 0      # RULEr_RESP_POS: resp [15:0], pos [23:16], any [24]
    ctl: int = 0           # RULEr_CTL

    def f(self, lsb, w, word="ctl"):
        return (getattr(self, word) >> lsb) & ((1 << w) - 1)


class Rules:
    def __init__(self, rules=None, rule_clk=0):
        self.r = rules or [Rule(), Rule()]
        self.rule_clk = rule_clk
        self.hist = self.cnt = 0
        self.hits = [0, 0]
        self.clk_q = self.dat_q = 0
        self.tk = 0
        self.drv_en = self.drv_val = 0
        self.rsr = self.left = self.wait = self.pending = 0
        self.hit_pulse = self.abort_pulse = 0

    def step(self, frame_rst=0, bit_valid=0, bit_in=0, bit_start=0,
             pin_clk=0, pin_dat=0, crc_ok=0, tx_idle=0, seq_wr=0):
        src, edge = self.rule_clk & 1, (self.rule_clk >> 3) & 1
        tdly = (self.rule_clk >> 4) & 0xFF
        hold = (self.rule_clk >> 12) & 1
        cedge = (not pin_clk and self.clk_q) if edge else (pin_clk and not self.clk_q)
        bv, bi = (cedge, self.dat_q) if src else (bit_valid, bit_in)
        delayed = src and tdly != 0
        bs = bit_start if not src else (self.tk == 1 if delayed else cedge)
        hist_n = ((self.hist << 1) | bi) & 0xFFFF

        match = [0, 0]
        for k, r in enumerate(self.r):
            cond = r.f(8, 2)
            ok = (1, crc_ok, not crc_ok, tx_idle)[cond]
            pat, msk = r.f(0, 16, "match"), r.f(16, 16, "match")
            at = r.f(24, 1, "resp_pos") or r.f(16, 8, "resp_pos") == self.cnt
            chained = k == 0 or not r.f(13, 1) or self.hits[0]
            match[k] = int(bool(r.f(12, 1) and bv and ok and at and chained
                                and ((hist_n ^ pat) & msk) == 0))
        win = 0 if match[0] else 1 if match[1] else None

        self.hit_pulse = int(win is not None)
        self.abort_pulse = int(win is not None and self.r[win].f(11, 1))
        if delayed and cedge:
            tk = tdly
        else:
            tk = self.tk - 1 if self.tk else 0
        if frame_rst:
            self.hist = self.cnt = 0
        elif bv:
            self.hist = hist_n
            self.cnt = min(self.cnt + 1, 255)
        self.hits = [0, 0] if frame_rst else [h | m for h, m in zip(self.hits, match)]

        if win is not None and self.r[win].f(10, 1):
            r = self.r[win]
            self.rsr, self.left = r.f(0, 16, "resp_pos"), r.f(0, 4)
            if r.f(4, 4) == 0 and not delayed:
                self.drv_en, self.pending = 1, 0
            else:
                self.pending, self.wait = 1, r.f(4, 4) + int(bool(delayed))
        elif bs:
            if self.pending:
                self.wait -= 1
                if self.wait == 0:
                    self.pending, self.drv_en = 0, 1
            elif self.drv_en:
                if self.left == 0:
                    self.drv_en = hold
                else:
                    self.rsr >>= 1
                    self.left -= 1
        if hold and seq_wr and not (win is not None and self.r[win].f(10, 1)):
            self.drv_en = 0
        self.drv_val = self.rsr & 1
        self.tk = tk
        self.clk_q, self.dat_q = pin_clk, pin_dat
