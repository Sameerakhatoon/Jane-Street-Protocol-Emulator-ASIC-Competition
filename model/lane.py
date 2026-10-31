"""One lane: sequencer, timer, response rules and the class L configuration bank.

The assists not modelled yet (CRC, stuffer, codec, DPLL, TX and RX engines)
enter as per-cycle inputs in LaneIn. A CSRW to a class L word is accepted at
the end of its dispatch cycle c and used from c + 2.
"""
from dataclasses import dataclass

from .isa import CLASS_L
from .rules import Rule, Rules
from .seq import Seq, SeqCfg, SeqIn
from .timer import Timer

LANE_CFG, LANE_PROG, GUARD01, GUARD23 = 0x18, 0x19, 0x1A, 0x1B
TIMER_INC, TIMER_CTL, RULE_CLK, STATUS, CMD, CLOCK, TS = 0x14, 0x15, 0x48, 0x23, 0x24, 0x26, 0x28


@dataclass
class LaneIn:
    pins: int = 0          # synchronized virtual pin inputs
    ev_ext: int = 0        # EV sources from unmodelled assists (bit 2 RULEHIT is ours)
    flg: int = 0           # SAMPLE, HALF, EDGE, SYNCED from the DPLL
    chip: int = 0
    bit_valid: int = 0     # recovered bit for the rules (bit mode)
    bit_in: int = 0
    bit_start: int = 0
    crc_ok: int = 0
    tx_idle: int = 1
    h_tx_we: int = 0
    h_tx_data: int = 0
    h_rx_re: int = 0


class Lane:
    def __init__(self, mem, lane=0, cfg=None, entries=2, lat=1, dwin=None, ts=None):
        self.lane = lane
        self.cfg = dict(cfg or {})
        self.ts = ts or (lambda: self.seq.cycle)
        self.timer = Timer()
        self.rules = Rules([Rule(), Rule()])
        self.pending = []                    # (apply cycle, addr, value)
        self.abort = 0                       # sticky ABORT from a rule abort
        self.seq = Seq(mem, lane, entries=entries, lat=lat, csr_read=self._read, dwin=dwin)
        self._apply_cfg()
        self.seq.pc = self.seq.cfg.start_pc
        self.seq.fetch.tags.clear()
        self.seq.next_at = self.seq.fetch.need(0, self.seq.pc >> 1)

    def _apply_cfg(self):
        g = self.cfg.get
        self.seq.cfg = SeqCfg.from_words(g(LANE_CFG, 0), g(GUARD01, 0), g(GUARD23, 0),
                                         g(LANE_PROG, 0))
        self.timer.inc, self.timer.ctl = g(TIMER_INC, 0) & 0xFFFFFF, g(TIMER_CTL, 0)
        for k in range(2):
            r = self.rules.r[k]
            r.match, r.resp_pos, r.ctl = g(0x40 + 4 * k, 0), g(0x41 + 4 * k, 0), g(0x42 + 4 * k, 0)
        self.rules.rule_clk = g(RULE_CLK, 0)

    def events(self, ev_ext):
        ev = ev_ext & ~0x05
        ev |= int(any(self.rules.hits)) << 2
        ev |= self.abort << 7
        ev |= (self.timer.flag if self.timer.ev0_sel else ev_ext & 1)
        return ev

    def status(self):
        s = self.seq
        return (self._ev | s.tflag << 8 | s.illegal << 9 | (1 - s.halted) << 10
                | self.rules.hits[0] << 11 | self.rules.hits[1] << 12 | self.abort << 21
                | s.aflag << 22
                | self.timer.flag << 23 | s.pc << 24)

    def _read(self, addr):
        if addr == STATUS:
            return self.status()
        if addr == CLOCK:
            return self.seq.clk
        if addr == TS:
            return self.ts()
        return 0

    def step(self, i):
        c = self.seq.cycle
        self._ev = self.events(i.ev_ext)
        lc = self.cfg.get(LANE_CFG, 0)
        o = self.seq.step(SeqIn(pins=i.pins, ev=self._ev, flg=i.flg, chip=i.chip,
                                h_tx_we=i.h_tx_we, h_tx_data=i.h_tx_data, h_rx_re=i.h_rx_re))
        cmd = o.cmd
        if o.csr_we:
            addr, val = o.csr_we
            if addr in CLASS_L:
                self.pending.append((c + 2, addr, val))
            elif addr == CMD:
                cmd |= val & 0xFF
        frame_rst = cmd & 1
        if frame_rst:
            self.abort = 0
        elif self.rules.abort_pulse:         # the registered pulse of the last cycle
            self.abort = 1
        clk_pin, rxpin = (self.rules.rule_clk >> 1) & 3, (lc >> 2) & 3
        self.rules.step(frame_rst, i.bit_valid, i.bit_in, i.bit_start,
                        (i.pins >> clk_pin) & 1, (i.pins >> rxpin) & 1, i.crc_ok, i.tx_idle,
                        (o.pin_wr >> (lc & 3)) & 1)
        self.timer.step(frame_rst, o.timer_ack)
        due = [p for p in self.pending if p[0] == c + 1]
        for _, addr, val in due:
            self.cfg[addr] = val
        if due:
            self.pending = [p for p in self.pending if p[0] != c + 1]
            self._apply_cfg()
        o.cmd = cmd
        return o

    @property
    def vout(self):
        lc = self.cfg.get(LANE_CFG, 0)
        txpin, coded = lc & 3, (lc >> 14) & 1
        p = self.seq.pins
        if self.rules.drv_en and not coded:
            p = p & ~(1 << txpin) | self.rules.drv_val << txpin
        return p & 0xF

    @property
    def voe(self):
        lc = self.cfg.get(LANE_CFG, 0)
        d = self.seq.dirs
        if self.rules.drv_en and not (lc >> 14) & 1:
            d |= 1 << (lc & 3)
        return d & 0xF
