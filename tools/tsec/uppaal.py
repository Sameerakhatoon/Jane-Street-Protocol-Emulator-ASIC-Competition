"""UPPAAL XML export of one compiled role.

One template per role: a location before each timed action with the spec's
tolerance as guard (lower bound, rounded up) and invariant (upper bound,
rounded down), an environment template that offers the trigger, and the
deadlock query. Optional: nothing in the build depends on UPPAAL (licence).
"""
import xml.etree.ElementTree as ET

from .units import lower_bound, upper_bound


def export(role, low):
    f = low.frame
    nta = ET.Element("nta")
    ET.SubElement(nta, "declaration").text = "chan go;"
    tpl = ET.SubElement(nta, "template")
    ET.SubElement(tpl, "name").text = role.name.text
    ET.SubElement(tpl, "declaration").text = "clock x;"
    acts = [(ins.ob, v) for ins in low.prog if ins.ob for v in range(ins.ob.reps)]
    base = acts[0][0].ideals[0]

    def loc(lid, name, inv=None):
        el = ET.SubElement(tpl, "location", id=lid)
        ET.SubElement(el, "name").text = name
        if inv:
            ET.SubElement(el, "label", kind="invariant").text = inv

    def edge(src, dst, guard=None, assign=None, sync=None):
        tr = ET.SubElement(tpl, "transition")
        ET.SubElement(tr, "source", ref=src)
        ET.SubElement(tr, "target", ref=dst)
        for kind, text in (("guard", guard), ("synchronisation", sync), ("assignment", assign)):
            if text:
                ET.SubElement(tr, "label", kind=kind).text = text

    loc("idle", "idle")
    for k, (ob, v) in enumerate(acts):
        hi = upper_bound(ob.ideals[v] - base + ob.tol) if k else 0
        loc(f"a{k}", f"before_{ob.name.replace(' ', '_')}_{v}", f"x <= {hi}")
    end = acts[-1][0].ideals[-1] - base + f.tail_gap
    loc("done", "done", f"x <= {upper_bound(end)}")
    ET.SubElement(tpl, "init", ref="idle")
    edge("idle", "a0", sync="go?", assign="x = 0")
    for k, (ob, v) in enumerate(acts):
        lo = lower_bound(ob.ideals[v] - base - ob.tol) if k else 0
        nxt = f"a{k + 1}" if k + 1 < len(acts) else "done"
        edge(f"a{k}", nxt, guard=f"x >= {max(lo, 0)}")
    edge("done", "idle", guard=f"x >= {lower_bound(end - f.tail_tol)}")
    env = ET.SubElement(nta, "template")
    ET.SubElement(env, "name").text = "Env"
    el = ET.SubElement(env, "location", id="e0")
    ET.SubElement(el, "name").text = "e0"
    ET.SubElement(env, "init", ref="e0")
    tr = ET.SubElement(env, "transition")
    ET.SubElement(tr, "source", ref="e0")
    ET.SubElement(tr, "target", ref="e0")
    ET.SubElement(tr, "label", kind="synchronisation").text = "go!"
    ET.SubElement(nta, "system").text = f"P = {role.name.text}();\nE = Env();\nsystem P, E;"
    qs = ET.SubElement(nta, "queries")
    q = ET.SubElement(qs, "query")
    ET.SubElement(q, "formula").text = "A[] not deadlock"
    ET.SubElement(q, "comment").text = "every timing window can be met (no timelock)"
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(nta, encoding="unicode")
