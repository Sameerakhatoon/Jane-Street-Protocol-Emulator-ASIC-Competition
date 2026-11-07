# SPDX-License-Identifier: Apache-2.0
"""Lane IR: lane instructions before addresses, with labels, tunable delays and tags.

An Ins is one ISA instruction (mnemonic and fields) whose jump target is
still a label. A DLY marked `tune` is chosen later by a delay solver, and
`ob` tags the obligation the instruction discharges. A WAIT or a blocking
PULL that waits for the environment carries a Wait record (what it waits
for and why), and a pin test carries the wire it reads. Both lowerings (the
paced frame in lower.py, state machines in fsm.py) produce this IR; layout,
timing analysis and code generation read only the IR, never the source.
"""
from dataclasses import dataclass, field


@dataclass(eq=False)
class Ins:
    op: str
    a: dict
    ob: object = None           # the obligation this instruction discharges
    tune: bool = False          # a delay the solver may change
    label: str = None
    note: str = ""
    wait: object = None         # Wait: what a WAIT or blocking PULL waits for
    reads: str = None           # wire a pin test reads (JMP PIN, WAIT with timeout 0)
    serves: object = None       # tunable DLY: the instruction whose obligation it places
    sep: object = None          # Sep: minimum distance from an earlier instruction


@dataclass(eq=False)
class Wait:
    kind: str                   # edge (the event), pre (level before an edge), test, pull, alt
    event: object = None        # ast.Event the edge wait completes on
    wire: str = None
    level: int = None
    abort: object = None        # Abort: the seq's `abort on` this wait also ends on


@dataclass(eq=False)
class Abort:
    event: object               # ast.Event
    target: str                 # label of the abort state
    mode: str                   # level (LANE_CFG alternate is a pin level) or change
    check_wire: str = None      # change mode: wire whose level tells the event from others
    check_level: int = None


@dataclass(eq=False)
class Sep:
    ref: object                 # the earlier Ins
    ticks: object               # Fraction: the least distance at the pins
    span: object = None
    text: str = ""
    skew: bool = False          # different pins: pad skew counts against the distance
    hi: object = None           # a window after our own edge also has a largest distance
    kind: str = "out"           # in: the instruction samples, the distance ends at its sample


@dataclass(eq=False)
class Pace:
    """The instruction performs this timed statement of a paced chain (pace.Chain)."""
    chain: object
    stmt: object
    entry: int = None           # the timeline entry, when the code is unrolled and reordered


@dataclass(eq=False)
class WinOb:
    """OB-WIN on one instruction: its effect at the pins within [lo, hi] ticks after event."""
    code: str
    name: str
    span: object
    kind: str                   # out: a pin change; in: a sample
    event: object               # ast.Event the window is measured from
    lo: object                  # Fraction ticks
    hi: object
    lo_expr: object = None      # the source expressions, for the fix line
    hi_expr: object = None
    source: str = ""            # respond NAME, or the statement


def listing(prog, labels=None):
    """The IR as text: one instruction per line with label, tags and notes."""
    from . import isa
    out = []
    for i, ins in enumerate(prog):
        lab = f"{ins.label}:" if ins.label else ""
        text = isa.fmt(ins.op, {k: v for k, v in ins.a.items() if k != "expect_taken"})
        tags = []
        if ins.tune:
            tags.append("tunable")
        if ins.wait is not None:
            w = ins.wait
            what = w.kind + (f" {w.wire}={w.level}" if w.wire is not None else "")
            tags.append(what + (" alt" if w.abort is not None else ""))
        if ins.reads:
            tags.append(f"reads {ins.reads}")
        if ins.ob is not None:
            tags.append(f"{ins.ob.code} {ins.ob.name}")
        if ins.sep is not None:
            tags.append(f"sep >= {float(ins.sep.ticks):.2f}")
        note = "; ".join(tags + ([ins.note] if ins.note else []))
        out.append(f"{i:3d}  {lab:<14}{text:<28} {note}".rstrip())
    return out
