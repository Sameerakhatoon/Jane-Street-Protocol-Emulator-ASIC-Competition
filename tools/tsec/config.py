"""Stage 6c, lane configuration: the register values an image needs beside its program.

Per lane: the assists' registers (assists.py), LANE_CFG (the pin JMP PIN
tests, the WAIT alternate of an `abort on`), the DPLL period of bit-timer
pacing, and per wire its virtual pin and the PAD_OUT value of the pad it
goes to (source, open drain, idle level and idle drive, decision of the
spec's wire attributes; the pad number itself is the board plan's). Every
value is packed by tools.tseasm.regs, so it is range-checked against the
register table. The loader writes these before RUN.
"""
import json

from . import assists
from ..tseasm import regs
from .diag import TseError


def hexw(v):
    return f"0x{v:08X}"


def lane_config(sym, res, fclk):
    """(regs, pins, checks, notes) of one compiled role; R303, R304, R318, R319 on lane limits."""
    role, lane, att = res.role, res.lane, res.chosen
    if res.kind == "fsm":
        f = att.fsm
        vin, vout = f.pin_in, f.pin_out
        cfg = {}
        if f.jmp_pin is not None:
            cfg["jmp_pin"] = vin[f.jmp_pin]
        if f.alt is not None:
            cfg.update(alt_pol=f.alt[0], alt_src=f.alt[1], alt_idx=f.alt[2])
        notes = dict(f.host_notes)
    else:
        uses = [u.text for u in role.uses]
        vin = vout = {w: k for k, w in enumerate(uses)}
        cfg, notes = {}, {}
        sampled = [ins for ins in att.low.prog if ins.op == "JMP" and ins.a["cond"] == "PIN"]
        if sampled and sampled[0].ob is not None:
            cfg["jmp_pin"] = vin[sampled[0].ob.stmt.wire.text]
    try:
        values, checks = assists.lane_config(sym, role, fclk, lambda w: vin.get(w, 0))
    except TseError:
        raise
    values["LANE_CFG"] = regs.pack("LANE_CFG", **cfg)
    if res.kind != "fsm" and att.low.nco_period:
        values["DPLL_PERIOD"] = regs.pack("DPLL_PERIOD", period=att.low.timer_q16)
    pins = {}
    for u in role.uses:
        w = sym.wires[u.text]
        entry = {"dir": w.dir}
        if u.text in vin and w.dir in ("in", "inout"):
            entry["vpin_in"] = vin[u.text]
        if u.text in vout and w.dir in ("out", "inout"):
            idle = w.attr("idle")
            lvl = 1 if w.open_drain else (int(sym.eval(idle.value).v) if idle else 0)
            entry["vpin_out"] = vout[u.text]
            entry["PAD_OUT"] = hexw(regs.pack("PAD_OUT", src=lane * 4 + vout[u.text],
                                              od=int(w.open_drain), en=1, idle=lvl,
                                              idle_oe=0 if w.open_drain else 1))
        pins[u.text] = entry
    return values, pins, checks, notes


def image_json(sym, lanes, fclk):
    """lanes: [(lane, role, regs, pins, checks, notes)]."""
    out = {"protocol": sym.spec.name.text, "fclk_hz": int(fclk), "lanes": []}
    for lane, role, values, pins, checks, notes in lanes:
        out["lanes"].append({"lane": lane, "role": role.name.text,
                             "regs": {k: hexw(v) for k, v in sorted(values.items(), key=reg_order)},
                             "pins": pins, "checks": checks, "host": notes})
    return json.dumps(out, indent=2) + "\n"


def reg_order(kv):
    r = regs.LANE.get(kv[0])
    return (r["addr"] if r else 999, kv[0])
