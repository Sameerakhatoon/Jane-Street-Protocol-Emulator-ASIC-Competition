# SPDX-License-Identifier: Apache-2.0
"""The timing analysis against the sequencer reference model, on the shipped SPI target specs.

For every accepted build of the SPI targets in specs/tests/expected-outcomes.txt the compiled
program runs on the reference lane (verif/seqspec/seqspec_ref.py, which the lockstep suites
check against the RTL) under random legal controllers: the CS fall in any slot phase, SCK half
periods and the CS setup at the datasheet minimum or longer, a pad delay of 0 to 25 ns per pin,
whole frames and frames cut short by CS rising, and data steering the decode. For every action
with a window obligation the distance D from the cycle its trigger edge became visible to the
lane to the cycle the action dispatched must lie in the compiler's [dmin, dmax], every trigger
edge is answered at most once, and in a frame of whole bytes the byte loop samples once per
sampling edge. The datasheet minimums come from the spec's rules; nothing else is shared with
the analysis. TSEC_REPLAY_TRIALS sets the replays per build, four frames each (default 40)."""
import math
import os
import random
import sys
from fractions import Fraction as Fr

import pytest

from tools.tsec import ast as A
from tools.tsec import isa
from tools.tsec.cycles import FetchModel
from tools.tsec.fsmsched import Scheduler
from tools.tsec.semantics import base_event, min_separation

from helpers import front
from test_14_soundness import lane_cfg, words

sys.path.insert(0, "verif/seqspec")
import seqspec_ref as ref          # noqa: E402

TRIALS = int(os.environ.get("TSEC_REPLAY_TRIALS", "40"))
PAD = 25e-9                        # the compiler's default pad delay range, 0 to 25 ns

# (spec, overrides, fclk, most bytes in a frame, one sample per sampling edge in a frame of
# whole bytes: true for the targets whose only loop is the byte loop)
BUILDS = [
    ("spi_target", {"t_css": "142e-9", "f_sck": "1.875e6"}, 60e6, 3, True),
    ("spi_target_mode0_css142", {"t_css": "142e-9", "f_sck": "1.875e6"}, 60e6, 3, True),
    ("spi_target_mode2", {"t_css": "142e-9", "f_sck": "1.875e6"}, 60e6, 3, True),
    ("spi_target_mode1", {"t_css": "109e-9", "f_sck": "1.9e6"}, 60e6, 3, True),
    ("spi_target_mode3", {"t_css": "109e-9", "f_sck": "1.9e6"}, 60e6, 3, True),
    ("spi_flash_target", {}, 60e6, 9, False),
    ("spi_flash_target", {"t_css": "206e-9"}, 50e6, 9, False),
    ("spi_flash_target", {"f_sck": "7e5"}, 60e6, 9, False),
]


def ev(kind, wire):
    return A.Event(kind, A.Ident(wire))


class Build:
    """One accepted build: its program, its obligations and the controller's minimums."""

    def __init__(self, spec, over, fclk, nbytes):
        self.nbytes = nbytes
        with open(f"specs/{spec}.tse") as fh:
            sym = front(fh.read(), **over)
        role = sym.roles[0]
        self.att, _ = Scheduler(sym, role, 0, Fr(int(fclk)), FetchModel(), (0, 25)).compile()
        assert self.att is not None, f"{spec} {over} is not accepted"
        assert not any(i.op == "TMR" for i in self.att.prog), "the replay sets no guard registers"
        self.words, self.cfg = words(self.att), lane_cfg(self.att)
        f = self.att.fsm
        self.cs, self.sck = 1 << f.pin_in["cs_n"], 1 << f.pin_in["sck"]

        def sep(a, b):
            return float(min_separation(sym, role, a, b, Fr(int(fclk)))[0])
        rise_first = sep(ev("fall", "cs_n"), ev("rise", "sck")) > 0
        self.idle = 0 if rise_first else 1          # SCK level while CS is high
        self.css = sep(ev("fall", "cs_n"), ev("rise" if rise_first else "fall", "sck"))
        self.half = (sep(ev("fall", "sck"), ev("rise", "sck")), sep(ev("rise", "sck"), ev("fall", "sck")))
        assert self.css > 0 and min(self.half) > 0
        self.pad = PAD * fclk
        self.obs = []                                # (pc, wire, kind, dmin, dmax, line)
        for st in self.att.res.obs.values():
            if st.dmin is None:
                continue
            base, _ = base_event(sym, st.ob.event)
            if base.kind in ("rise", "fall"):
                pc = next(k for k, i in enumerate(self.att.prog) if i is st.ins)
                self.obs.append((pc, base.name.text, base.kind, st.dmin, st.dmax, st.ob.span.line))
        assert self.obs
        # the sampling edge of the byte loop: the edge the IN PINS instructions are anchored on
        self.samples = {pc for pc, *_ in self.obs if self.att.prog[pc].op == "IN"}
        labels = self.att.labels
        self.abort_pcs = {labels[i.a["target"]] for i in self.att.prog if i.op == "JMP"
                          and (getattr(i, "abort_pin", False) or getattr(i, "abort_jump", False))}
        assert self.abort_pcs
        imms = sorted({i.a["imm"] for i in self.att.prog if i.op == "SET" and i.a.get("dst") == "Y"})
        self.steer = imms or [0]


def frame_times(b, rng, t0):
    """One controller frame from t0 (ticks, at the pins): [(time, wire, level)], CS rise time,
    whole bytes (None if cut short)."""
    def extra(base):
        r = rng.random()
        return 0.0 if r < 0.4 else rng.uniform(0, 0.5) * base if r < 0.9 else rng.uniform(0, 4) * base
    nbytes = rng.randint(1, b.nbytes)
    n = 16 * nbytes
    cut = rng.randint(1, n - 1) if rng.random() < 0.3 else None
    t = t0
    out = [(t, "cs_n", 0)]
    t += b.css + extra(b.css)
    level = b.idle
    for k in range(cut or n):
        if k:
            t += b.half[level] + extra(b.half[level])
        level = 1 - level
        out.append((t, "sck", level))
    last = t
    rise = last + rng.uniform(0, 2) * min(b.half)
    out.append((rise, "cs_n", 1))
    if level != b.idle:                             # back to the idle level after a cut
        out.append((max(rise, last + b.half[level]) + extra(b.half[level]), "sck", b.idle))
    return out, rise, None if cut else nbytes


def lane_trace(b, rng, frames):
    """Pin events of `frames` frames mapped to the cycles the lane sees them: a pad delay of
    0 to 25 ns per pin, then the first clock edge after it."""
    delay = {w: rng.uniform(0, b.pad) for w in ("cs_n", "sck")}
    t, evs, info = rng.uniform(20, 40), [], []
    for _ in range(frames):
        t += rng.uniform(150, 400)                  # CS high long enough for the lane to rearm
        fr, rise, whole = frame_times(b, rng, t)
        evs += fr
        info.append((t, rise, whole))
        t = max(x for x, _, _ in fr)
    vis = sorted((math.ceil(x + delay[w]), w, v) for x, w, v in evs)
    vinfo = [(math.ceil(f + delay["cs_n"]), math.ceil(r + delay["cs_n"]), whole) for f, r, whole in info]
    return vis, vinfo, vis[-1][0] + 400


def pins_per_cycle(b, vis, ncyc):
    pins, out, k = b.cs | (b.sck if b.idle else 0), [], 0
    for c in range(ncyc):
        while k < len(vis) and vis[k][0] <= c:
            _, w, v = vis[k]
            bit = b.cs if w == "cs_n" else b.sck
            pins = (pins | bit) if v else (pins & ~bit)
            k += 1
        out.append(pins)
    return out


def replay(b, rng, frames=4):
    """Run frames on the reference lane; returns the violations and the D values seen."""
    vis, info, ncyc = lane_trace(b, rng, frames)
    pins = pins_per_cycle(b, vis, ncyc)
    dval = rng.choice(b.steer) if rng.random() < 0.8 else rng.getrandbits(16)
    env = [ref.Env(p, 0, 0, 0, 0, 1, dval) for p in pins]
    rows = ref.run(b.words, env, 0, FetchModel().lat, 0, b.cfg, 0, 0, ncyc)
    # once a wait ends on the alternate (CS rose) the lane runs toward its abort state, where
    # waits for a level that already holds end at once: no window applies until it gets there,
    # as in the analysis. The flag holds until the next WAIT issues, so only the instruction
    # right after a WAIT reads it: the ones after the abort state would see it still set
    issued, pending, after_wait = {}, False, False
    for c, r in enumerate(rows):
        if not r["issue"]:
            continue
        pending = (pending or (after_wait and bool(r["aflag"]))) and r["pc"] not in b.abort_pcs
        after_wait = b.att.prog[r["pc"]].op == "WAIT"
        if not pending:
            issued.setdefault(r["pc"], []).append(c)
    bad, seen = [], {}
    for pc, wire, kind, dmin, dmax, line in b.obs:
        occ = [c for c, w, v in vis if w == wire and v == (kind == "rise")]
        used = set()
        for c in issued.get(pc, []):
            prior = [v for v in occ if v <= c]
            if not prior:
                bad.append(f"line {line}: pc {pc} at cycle {c} before any {kind}({wire})")
                continue
            d = c - prior[-1]
            seen.setdefault(line, set()).add(d)
            if not dmin <= d <= dmax:
                bad.append(f"line {line}: pc {pc} dispatched {d} cycles after {kind}({wire}) "
                           f"(cycle {prior[-1]}), the compiler claims [{dmin}, {dmax}]")
            if prior[-1] in used:
                bad.append(f"line {line}: pc {pc} acted twice on the {kind}({wire}) of cycle {prior[-1]}")
            used.add(prior[-1])
    return bad, seen, (vis, info, issued)


def whole_byte_counts(b, vis, info, issued):
    """In each frame of whole bytes: sampling edges and samples, which must be equal."""
    out = []
    edges = {(wire, kind) for pc, wire, kind, *_ in b.obs if pc in b.samples}
    for fall, rise, whole in info:
        if whole is None:
            continue
        # an edge seen in the same cycle as the CS rise still counts: no CS hold time applies,
        # and a WAIT whose pin condition and alternate hold together ends on the pin
        n_edges = sum(1 for c, w, v in vis if fall < c <= rise and (w, "rise" if v else "fall") in edges)
        n_samp = sum(1 for pc in b.samples for c in issued.get(pc, []) if fall < c <= rise + 8)
        out.append((n_edges, n_samp))
    return out


@pytest.mark.parametrize("spec,over,fclk,nbytes,count", BUILDS,
                         ids=[f"{s}-{'-'.join(o.values()) or 'default'}-{f / 1e6:.0f}MHz"
                              for s, o, f, *_ in BUILDS])
def test_reference_lane_within_the_claimed_distances(spec, over, fclk, nbytes, count):
    b = Build(spec, over, fclk, nbytes)
    rng = random.Random(f"{spec}{over}{fclk}")
    bad, seen = [], {}
    for _ in range(TRIALS):
        v, s, (vis, info, issued) = replay(b, rng)
        bad += v
        for k, ds in s.items():
            seen.setdefault(k, set()).update(ds)
        if count:
            bad += [f"a frame of whole bytes has {e} sampling edges but {n} samples"
                    for e, n in whole_byte_counts(b, vis, info, issued) if e != n]
    assert not bad, "\n".join(sorted(set(bad))[:20])
    assert set(seen) == {line for *_, line in b.obs}, "every obligation is exercised"


def test_a_claim_one_cycle_off_is_caught():
    # planted bug: every claimed range one cycle later than the analysis found
    b = Build(*BUILDS[0][:4])
    b.obs = [(pc, w, k, lo + 1, hi + 1, line) for pc, w, k, lo, hi, line in b.obs]
    bad = replay(b, random.Random(0))[0]
    assert {v.split(":")[0] for v in bad} == {f"line {line}" for *_, line in b.obs}


def test_a_late_program_is_caught():
    # planted bug: the pad DLY 0 in front of the byte loop's first edge wait becomes DLY 12, so
    # the lane reaches that wait after the first SCK edge
    b = Build(*BUILDS[0][:4])
    first = min(pc for pc in b.samples)
    k = max(pc for pc in range(first) if b.att.prog[pc].op == "DLY")
    assert b.att.prog[k].a["d"] == 0
    b.words[k] = isa.encode("DLY", dict(b.att.prog[k].a, d=12))
    rng = random.Random(0)
    bad = []
    for _ in range(4):
        v, _, (vis, info, issued) = replay(b, rng)
        bad += v + [1 for e, n in whole_byte_counts(b, vis, info, issued) if e != n]
    assert bad
