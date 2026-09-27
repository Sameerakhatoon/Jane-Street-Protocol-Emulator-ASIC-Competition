"""Cycle-exact reference model of one lane sequencer (ISA version 1).

One step() is one clock cycle; cycle 0 is the frame boundary at which RUN took
effect. Effects of an instruction dispatched in cycle c are visible from c + 1
(LDW's OSR excepted). The successor dispatches in max(c + d, f), where f is c
on a buffer hit and s + lat on a miss, s the first own slot at or after c.
For WAIT and blocking PUSH or PULL, d is only known when they end, but the
successor's fetch still starts at dispatch.
"""
from dataclasses import dataclass

from .fetch import Fetch, first_data_slot
from .isa import CLASS_L, Illegal, decode

M16, M32 = 0xFFFF, 0xFFFFFFFF


@dataclass
class SeqCfg:
    side_en: int = 0
    ss_base: int = 0
    jmp_pin: int = 0
    alt_pol: int = 0
    alt_src: int = 0
    alt_idx: int = 0
    gt: tuple = (0, 0, 0, 0)
    start_pc: int = 0

    @classmethod
    def from_words(cls, lane_cfg=0, guard01=0, guard23=0, lane_prog=0):
        b = lambda lsb, w: (lane_cfg >> lsb) & ((1 << w) - 1)
        return cls(side_en=b(6, 1), ss_base=b(4, 2), jmp_pin=b(7, 2), alt_pol=b(22, 1),
                   alt_src=b(23, 2), alt_idx=b(25, 3),
                   gt=(guard01 & M16, guard01 >> 16, guard23 & M16, guard23 >> 16),
                   start_pc=lane_prog & 0xFF)


@dataclass
class SeqIn:
    pins: int = 0          # synchronized virtual pin levels, as WAIT sees them
    ev: int = 0            # the eight EV sources (event 0 already selected by ev0_sel)
    flg: int = 0           # FLAG sources from the assists; GUARD, HTXF, HRXE, ONE are internal
    chip: int = 0          # CF0..CF3, TRIG, TICK
    h_tx_we: int = 0
    h_tx_data: int = 0
    h_rx_re: int = 0


@dataclass
class SeqOut:
    dispatch: object = None    # pc dispatched this cycle
    csr_we: object = None      # (addr, data)
    csr_re: object = None      # addr
    cmd: int = 0
    irq: int = 0
    sig: object = None         # (mask, val)
    emit: object = None        # (code, x[7:0], cycle)
    timer_ack: int = 0


class Seq:
    def __init__(self, mem, lane=0, cfg=None, entries=2, lat=1, csr_read=None, dwin=None):
        self.mem, self.lane = mem, lane
        self.cfg = cfg or SeqCfg()
        self.csr_read = csr_read or (lambda addr: 0)
        self.dwin = dwin if dwin is not None else [0] * 128
        self.fetch = Fetch(lane, entries, lat)
        self.cycle = 0
        self.pc = self.cfg.start_pc
        self.x = self.y = self.t = self.clk = self.gsel = 0
        self.tflag = self.aflag = 0
        self.isr = self.osr = 0
        self.pins = self.dirs = 0
        self.tx_full = self.rx_full = 0
        self.tx_data = self.rx_data = 0
        self.irq = 0                    # registered copy of the last cycle's irq pulse
        self.halted = self.illegal = 0
        self.busy = None                # WAIT, PUSH or PULL still running
        self.data = None                # pending LDW or STW data-slot access
        self.next_at = self.fetch.need(0, self.pc >> 1)

    def instr(self, pc):
        w = self.mem[(pc >> 1) % len(self.mem)]
        return (w >> (16 * (pc & 1))) & M16

    def guard(self):
        return int(self.clk >= self.cfg.gt[self.gsel])

    def step(self, i):
        """Advance one cycle with inputs i; return this cycle's SeqOut."""
        o = SeqOut()
        self._cclr = 0
        self._tx_take = self._rx_put = 0
        irq_pulse = 0
        if not self.halted:
            if self.data and self.cycle == self.data["at"]:
                self._data_slot()
            if self.busy is not None:
                self._continue(i, o)
            elif self.cycle == self.next_at:
                self._dispatch(i, o)
            if self.data and self.cycle + 1 == self.data["apply"] and not self.data["store"]:
                self.osr = self.data["val"]
                self.data = None
            irq_pulse = o.irq
        if self._tx_take:
            self.tx_full = 0
        if i.h_tx_we:
            self.tx_full, self.tx_data = 1, i.h_tx_data & M32
        if i.h_rx_re:
            self.rx_full = 0
        if self._rx_put:
            self.rx_full, self.rx_data = 1, self._rx_put[1]
        self.irq = irq_pulse
        self.clk = 0 if self._cclr else min(self.clk + 1, M16)
        self.cycle += 1
        return o

    # conditions

    def _sel(self, i, src, idx):
        if src == 0:
            if idx < 4:
                return (i.pins >> idx) & 1
            return ((i.pins ^ self.busy["pins0"]) >> (idx - 4)) & 1
        if src == 1:
            return (i.ev >> idx) & 1
        if src == 2:
            internal = {0: self.guard(), 4: self.tx_full, 5: 1 - self.rx_full, 7: 1}
            return internal[idx] if idx in internal else (i.flg >> idx) & 1
        return (i.chip >> idx) & 1 if idx < 6 else 0

    def _wait_eval(self, i, o):
        b, f = self.busy, self.busy["f"]
        own = self._sel(i, f["src"], f["idx"]) == f["pol"]
        alt = f["alt"] and self._sel(i, self.cfg.alt_src, self.cfg.alt_idx) == self.cfg.alt_pol
        tmo = f["to"] and b["tc"] == 0
        if not tmo and f["to"]:
            b["tc"] -= 1
        if own or alt or tmo:
            self.aflag = int(not own and bool(alt))
            self.tflag = int(not own and not alt)
            if own and f["pol"] == 1 and f["src"] == 1 and f["idx"] == 0:
                o.timer_ack = 1
            self._end(f["delay"])

    def _end(self, delay):
        self.next_at = max(self.cycle + 1 + delay, self.busy["ready"])
        self.busy = None

    def _continue(self, i, o):
        kind = self.busy["kind"]
        if kind == "WAIT":
            self._wait_eval(i, o)
        elif kind == "PUSH" and not self.rx_full:
            self._push()
            self._end(0)
        elif kind == "PULL" and self.tx_full:
            self._pull()
            self._end(0)

    def _push(self):
        if not self.rx_full:
            self._rx_put = (1, self.isr)
        self.isr = 0

    def _data_slot(self):
        if self.data["store"]:
            self.dwin[self.data["addr"]] = self.data["val"]
            self.data = None
        else:
            self.data["val"] = self.dwin[self.data["addr"]]

    def _pull(self):
        self.osr = self.tx_data
        self._tx_take = 1

    # dispatch

    def _dispatch(self, i, o):
        c, pc = self.cycle, self.pc
        o.dispatch = pc
        try:
            name, ss, f = decode(self.instr(pc))
        except Illegal:
            self.halted = self.illegal = 1
            o.irq = 0x8
            return
        npc, d = (pc + 1) & 0xFF, 1
        x, y, pins, dirs = self.x, self.y, self.pins, self.dirs
        blocking = None
        if name == "JMP":
            cond = f["cond"]
            taken = (cond == 0 or cond == 1 and x == 0 or cond == 2 and x != 0
                     or cond == 3 and y != 0 or cond == 4 and x != y
                     or cond == 5 and (i.pins >> self.cfg.jmp_pin) & 1
                     or cond == 6 and (self.tflag or self.aflag)
                     or cond == 7 and self.guard())
            if cond == 2:
                self.x = (x - 1) & M16
            if cond == 3:
                self.y = (y - 1) & M16
            if taken:
                npc = f["target"]
        elif name == "WAIT":
            self.tflag = self.aflag = 0
            blocking = dict(kind="WAIT", f=f, tc=self.t, pins0=i.pins)
        elif name == "SET":
            dst, imm = f["dst"], f["imm"]
            m, v = imm >> 4, imm & 0xF
            if dst == 0:
                self.pins = (pins & ~m | v & m) & 0xF
            elif dst == 1:
                self.dirs = (dirs & ~m | v & m) & 0xF
            elif dst == 2:
                self.x = imm
            elif dst == 3:
                self.y = imm
            elif dst == 4:
                self.t = ((self.t << 8) | imm) & M16
            elif dst == 5:
                self.x = (imm << 8) | (x & 0xFF)
            elif dst == 6:
                o.irq = imm & 0xF
            else:
                o.cmd = imm
        elif name == "MOV":
            d = 1 + f["delay"]
            v = (x, y, i.pins & 0xF, self.isr & M16, self.osr & M16, self.t, ~x & M16,
                 self.clk)[f["src"]]
            dst = f["dst"]
            if dst == 0:
                self.x = v
            elif dst == 1:
                self.y = v
            elif dst == 2:
                self.pins = v & 0xF
            elif dst == 3:
                self.dirs = v & 0xF
            elif dst == 4:
                self.isr = v
            elif dst == 5:
                self.osr = v
            elif dst == 6:
                self.t = v
            else:
                npc = v & 0xFF
        elif name == "OUT":
            d = 1 + f["delay"]
            n = f["n1"] + 1
            bits = self.osr & ((1 << n) - 1)
            self.osr >>= n
            m4 = (1 << min(n, 4)) - 1
            dst = f["dst"]
            if dst == 0:
                self.pins = pins & ~m4 & 0xF | bits & m4
            elif dst == 1:
                self.dirs = dirs & ~m4 & 0xF | bits & m4
            elif dst == 2:
                self.x = bits & M16
            elif dst == 3:
                self.y = bits & M16
            elif dst == 5:
                self.t = bits & M16
        elif name == "IN":
            d = 1 + f["delay"]
            n = f["n1"] + 1
            v = (i.pins & 0xF, x, y, 0, M32, self.osr)[f["src"]]
            self.isr = ((self.isr << n) | (v & ((1 << n) - 1))) & M32
        elif name == "PUSH":
            if self.rx_full and f["blk"]:
                blocking = dict(kind="PUSH")
            else:
                self._push()
        elif name == "PULL":
            if self.tx_full:
                self._pull()
            elif f["blk"]:
                blocking = dict(kind="PULL")
            else:
                self.osr = x
        elif name == "CSRW":
            o.csr_we = (f["addr"], (y << 16) | x)
            d = 3 if f["addr"] in CLASS_L else 1
        elif name == "CSRR":
            o.csr_re = f["addr"]
            v = 0 if f["addr"] in CLASS_L else self.csr_read(f["addr"]) & M32
            self.y, self.x = v >> 16, v & M16
        elif name in ("LDW", "STW"):
            a = (x & 0x7F) if f["ind"] else f["addr"]
            s = first_data_slot(c, self.lane)
            # STW writes ISR in s (ISR cannot change before: the lane is stalled);
            # LDW reads in s and OSR holds the word from s + 1 + lat
            self.data = dict(at=s, addr=a, store=name == "STW", val=self.isr,
                             apply=s + 1 + self.fetch.lat)
            if s == c:
                self._data_slot()
            d = s + 1 - c if name == "STW" else s + 1 + self.fetch.lat - c
        elif name == "DLY":
            d = 1 + f["d"]
        elif name == "TMR":
            d = 1 + f["delay"]
            self._cclr = f["cclr"]
            if f["gwe"]:
                self.gsel = f["gsel"]
        elif name == "SIG":
            o.sig = (f["mask"], f["val"])
        elif name == "EMIT":
            d = 1 + f["delay"]
            o.emit = (f["code"], x & 0xFF, c)
        if self.cfg.side_en:
            b0, b1 = self.cfg.ss_base, (self.cfg.ss_base + 1) & 3
            p = self.pins & ~((1 << b0) | (1 << b1)) & 0xF
            self.pins = p | ((ss & 1) << b0) | (((ss >> 1) & 1) << b1)
        self.pc = npc
        ready = self.fetch.need(c, npc >> 1)
        if blocking is None:
            self.next_at = max(c + d, ready)
        else:
            blocking["ready"] = ready
            self.busy = blocking
            if name == "WAIT":
                self._wait_eval(i, o)
