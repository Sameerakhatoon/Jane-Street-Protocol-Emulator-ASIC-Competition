"""The build report: listing, obligation table and verdict per role.

The golden runs in tests/golden pin this text line for line.
"""
import math

from .codegen import listing_lines

STRATEGY_NAME = {"dly": "DLY pacing", "timer": "bit-timer pacing"}


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


def role_section(res, rows):
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
