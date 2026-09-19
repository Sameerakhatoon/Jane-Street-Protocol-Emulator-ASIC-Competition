"""Shared helpers for the model tests: assemble, run, and read pin waveforms."""
from model.isa import encode as E, pack
from model.seq import Seq, SeqCfg, SeqIn


def mem_of(prog):
    return pack(prog) + [0] * (128 - (len(prog) + 1) // 2)


def parked(prog):
    """Append a jump to itself so the lane parks after the program."""
    return list(prog) + [E("JMP", cond=0, target=len(prog))]


def run_seq(prog, cycles, lane=0, cfg=None, data=(), ins=None, **kw):
    """Run a program, keeping the host TX mailbox fed from data.
    Returns (seq, per-cycle pin register, per-cycle dispatch pc or None)."""
    s = Seq(mem_of(prog), lane, cfg or SeqCfg(), **kw)
    data = list(data)
    if data:
        s.tx_full, s.tx_data = 1, data.pop(0)
    pins, disp = [], []
    for c in range(cycles):
        i = ins(c) if ins else SeqIn()
        if data and not s.tx_full:
            i.h_tx_we, i.h_tx_data = 1, data.pop(0)
        pins.append(s.pins)
        disp.append(s.step(i).dispatch)
    return s, pins, disp


def runs(levels, bit=0):
    """(level, length) of each run of one pin, from its first change on."""
    v = [(p >> bit) & 1 for p in levels]
    out, start = [], None
    for c in range(1, len(v)):
        if v[c] != v[c - 1]:
            if start is not None:
                out.append((v[c - 1], c - start))
            start = c
    return out


def dispatch_cycles(disp):
    return [(c, pc) for c, pc in enumerate(disp) if pc is not None]


__all__ = ["E", "mem_of", "parked", "run_seq", "runs", "dispatch_cycles", "SeqCfg", "SeqIn"]
