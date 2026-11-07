# SPDX-License-Identifier: Apache-2.0
"""The build report: listing, obligation table and verdict per role.

The golden runs in tests/golden pin this text line for line.
"""
import math
from fractions import Fraction as Fr

from .codegen import listing_lines

STRATEGY_NAME = {"dly": "DLY pacing", "timer": "bit-timer pacing", "rolled": "rolled loops",
                 "unrolled": "unrolled loops", "frames": "frame engine"}


def header(sym, fclk, fm, ppm):
    out = [f"tsec: protocol '{sym.spec.name.text}', clock {float(fclk) / 1e6:g} MHz, S = {fm.S}, "
           f"fetch buffer {fm.entries} word(s), fetch_latency {fm.lat}, clock budget {ppm} ppm"]
    for name, (ty, q) in sym.params.items():
        out.append(f"  param {name} = {float(q.v):g} ({ty})")
    return out


def obligations(rep, f):
    out = ["obligations (cycles; worst case over all slot phases and buffer states):"]
    base = rep.rows[0].ob.ideals[0]
    for r in rep.rows:
        ob, ok = r.ob, "ok" if r.ok else "FAIL"
        if ob.ref == "anchor":
            out.append(f"  line {ob.line:2d} {ob.name:<12} #{r.visit}: sample in [{r.shown[0]}, "
                       f"{r.shown[1]}) after edge, window [{float(ob.window[0]):.1f}, "
                       f"{float(ob.window[1]):.1f}]  {ok}")
        else:
            out.append(f"  line {ob.line:2d} {ob.name:<12} #{r.visit}: at {int(r.shown[0]):5d}, ideal "
                       f"{float(ob.ideals[r.visit] - base):8.2f}, error {float(r.cost):5.2f} <= "
                       f"{float(r.budget):5.2f}  {ok}")
    if rep.hold_lo is not None:
        out.append(f"  hold after last edge >= {rep.hold_lo} cycles (needs "
                   f"{float(f.tail_limit):.1f})  ok")
    if rep.hold_hi is not None:
        out.append(f"  re-armed within {rep.hold_hi} cycles of the last sample (limit "
                   f"{math.floor(f.tail_limit)})  ok")
    return out


def fsm_section(res, rows, fclk, pads):
    role = res.role
    out = ["", f"== role {role.name.text} ({role.kind}) on lane {res.lane}"]
    att = res.chosen
    if att is None:
        out.append("REJECTED: no lowering meets every obligation (rule R1, all or nothing)")
        for t in res.tried:
            out.append(f"  strategy {t.mode} ({len(t.prog)} instructions, {t.res.states} analysis "
                       f"states): {len(t.violations)} obligation(s) unproven")
            for v in t.violations:
                out.append(f"    - {v.code} line {v.span.line}: {v.msg}")
        return out
    out.append(f"strategy: state machine, {STRATEGY_NAME.get(att.mode, att.mode)} "
               f"({att.res.states} analysis states)")
    out += listing_lines(rows)
    out += fsm_obligations(att, fclk, pads)
    out.append(f"ACCEPTED: {len(rows)} instructions, image "
               + " ".join(f"{w:04x}" for _, w, _, _, _ in rows))
    return out


def fsm_obligations(att, fclk, pads):
    from . import printer
    pmin, pmax = (Fr(p) * fclk / 10**9 for p in pads[:2])

    def ns(t):
        return f"{float(t / fclk * 10**9):.1f}"
    out = [f"obligations (worst case over every slot phase, buffer state and legal environment "
           f"timing; pads {pads[0]:g} to {pads[1]:g} ns):"]
    for ins in att.prog:
        ob = ins.ob
        if ob is not None:
            st = att.res.obs[id(ins)]
            if ob.kind == "out":
                lo, hi = st.dmin + 3 + pmin, st.dmax + 4 + pmax
            else:
                lo, hi = st.dmin + pmin, st.dmax + 1 + pmax
            out.append(f"  line {ob.span.line:2d} {ob.name:<14} after {printer.event(ob.event):<12} "
                       f"{ns(lo)} to {ns(hi)} ns, window [{ns(ob.lo)}, {ns(ob.hi)}], margins "
                       f"{ns(lo - ob.lo)} and {ns(ob.hi - hi)} ns  ok")
        rob = getattr(ins, "rule_ob", None)
        if rob is not None:
            r = att.fsm.rule
            td, lo, hi = r.td, r.lo, r.hi
            out.append(f"  line {rob.span.line:2d} {rob.name:<14} after {printer.event(rob.event):<12} "
                       f"{ns(lo)} to {ns(hi)} ns by response rule 0 (hold, tick_delay {td}), window "
                       f"[{ns(rob.lo)}, {ns(rob.hi)}], margins {ns(lo - rob.lo)} and "
                       f"{ns(rob.hi - hi)} ns  ok")
        if ins.sep is not None:
            got = att.res.seps[id(ins)]
            span = f"{got[1]} to {got[3]}" if ins.sep.hi is not None else f"{got[1]}"
            out.append(f"  line {ins.sep.span.line:2d} {ins.sep.text:<14} {span} cycles after our earlier "
                       f"edge, needs at least {float(ins.sep.ticks):.2f} ticks"
                       + (f" and at most {float(ins.sep.hi):.2f}" if ins.sep.hi is not None else "")
                       + (" with pad skew" if ins.sep.skew else "") + "  ok")
    for st, k, err, budget, where, ok, skew in getattr(att, "pace_rows", []):
        out.append(f"  line {st.span.line:2d} {printer.stmt(st, 0).rstrip(';')[:40]:<40} placement "
                   f"error {float(err):.2f} of {float(budget):.2f} ticks  ok")
    if att.res.abort_latency is not None:
        out.append(f"  abort: reaches the abort state within {att.res.abort_latency} cycles of the "
                   "alternate condition being seen (stated bound)")
    for k, v in sorted(att.fsm.host_notes.items()):
        out.append(f"  host contract, {k}: {v}")
    for w in att.fsm.window.rows():
        words = f"{w.addr}" if w.size == 1 else f"{w.addr} to {w.addr + w.size - 1}"
        out.append(f"  data window word {words}: {w.name}"
                   + (f", the host loads 0x{w.init:X} before RUN" if w.init is not None else ""))
    return out


def role_section(res, rows, fclk=None, pads=(0, 25)):
    if res.kind == "frames":                            # frame roles
        from .frametime import section
        return section(res, rows, fclk, pads)
    if res.kind == "fsm":
        return fsm_section(res, rows, fclk, pads)
    role = res.role
    out = ["", f"== role {role.name.text} ({role.kind}) on lane {res.lane}"]
    att = res.chosen
    if att is None:
        out.append("REJECTED: no lowering meets every obligation (rule R1, all or nothing)")
        for t in res.tried:
            out.append(f"  strategy {t.mode}:")
            for v in t.report.violations[:4]:
                out.append(f"    - {v.code} {v.text}")
            if len(t.report.violations) > 4:
                out.append(f"    - ... {len(t.report.violations) - 4} more")
            out.append(f"    hint: {t.hint}")
        return out
    line = f"strategy: {STRATEGY_NAME[att.mode]}"
    if att.mode == "timer":
        line += f" (DLY pacing rejected: {res.tried[0].report.violations[0].text})"
    out.append(line)
    out += listing_lines(rows)
    low = att.low
    if low.nco_period:
        out.append(f"assist config: DPLL_PERIOD = {low.timer_q16} (bit timer {low.timer_q16}/16 = "
                   f"{float(low.nco_period):.4f} cycles), resync off, phase "
                   f"{float(low.nco_phase):+.2f} cycles")
    out += obligations(att.report, low.frame)
    out.append(f"ACCEPTED: {len(rows)} instructions, image "
               + " ".join(f"{w:04x}" for _, w, _, _, _ in rows))
    return out


def verdict(accepted):
    return ["", "RESULT: " + ("all roles accepted, image ready" if accepted else
                             "REJECTED, no image written (all or nothing)")]
