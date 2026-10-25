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
from . import ast as A
from ..tseasm import regs
from .diag import TseError


def hexw(v):
    return f"0x{v:08X}"


def lane_config(sym, res, fclk):
    """(regs, pins, checks, notes) of one compiled role; R303, R304, R318, R319 on lane limits."""
    if res.kind == "frames":                            # frame roles
        from .frameconf import lane_config as frame_config
        return frame_config(sym, res, fclk)
    role, lane, att = res.role, res.lane, res.chosen
    if res.kind == "fsm":
        f = att.fsm
        vin, vout = f.pin_in, f.pin_out
        cfg = {}
        if f.jmp_pin is not None:
            cfg["jmp_pin"] = f.vin(f.jmp_pin)
        if f.alt is not None:
            cfg.update(alt_pol=f.alt[0], alt_src=f.alt[1], alt_idx=f.alt[2])
        if f.side:
            cfg.update(side_en=1, ss_base=f.pin_out[f.side[0]])
        notes = dict(f.host_notes)
        rule_regs = first_bit_rule(f) if f.rule is not None else {}
        if f.rule is not None:
            cfg["txpin"] = vout[f.rule.wire]
    else:
        uses = [u.text for u in role.uses]
        vin = vout = {w: k for k, w in enumerate(uses)}
        cfg, notes = {}, frame_notes(sym, role)
        sampled = [ins for ins in att.low.prog if ins.op == "JMP" and ins.a["cond"] == "PIN"]
        if sampled and sampled[0].ob is not None:
            cfg["jmp_pin"] = vin[sampled[0].ob.stmt.wire.text]
    try:
        values, checks = assists.lane_config(sym, role, fclk, lambda w: vin.get(w, 0))
    except TseError:
        raise
    if checks.pop("arb_en", None) is not None or any(k.endswith(".arb_en") for k in checks):
        cfg["arb_en"] = 1
        checks = {k: v for k, v in checks.items() if not k.endswith(".arb_en")}
    if res.kind == "fsm" and att.fsm.rule is not None:
        values.update(rule_regs)
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


def first_bit_rule(f):
    """Rule 0 for a respond whose first bit the rule drives: pin-edge mode on the respond's
    wire, any position, mask 0 (every counted edge matches), drive, hold."""
    ev = f.rule.event
    return {"RULE_CLK": regs.pack("RULE_CLK", src=1, clk_pin=f.pin_in[ev.name.text],
                                  edge=int(ev.kind == "fall"), tick_delay=f.rule.td, hold=1),
            "RULE0_MATCH": regs.pack("RULE0_MATCH", pattern=0, mask=0),
            "RULE0_RESP_POS": regs.pack("RULE0_RESP_POS", resp=0, pos=0, any=1),
            "RULE0_CTL": regs.pack("RULE0_CTL", rlen_m1=0, offset=0, cond="NONE", drive=1, en=1)}


def frame_notes(sym, role):
    """The host contract of a paced-frame role: bit order of the words it pushes and pulls.
    IN shifts ISR left and OUT shifts OSR right, so the first bit on the wire is the highest
    bit of a pushed word and the lowest bit of a pulled one."""
    notes = {}
    for st in role.states:
        for e in st.edges:
            for s in getattr(e, "body", None) or []:
                if not isinstance(s, A.Shift):
                    continue
                v = s.var.text
                w = int(sym.eval(s.count).v)
                if s.out:
                    notes[f"pull host {v}"] = (f"bits {w - 1} to 0, bit 0 goes first" if s.order == "lsb"
                                               else f"bits {w - 1} to 0 bit-reversed: bit 0 goes first and "
                                               "the spec sends the msb first")
                else:
                    notes[f"push host {v}"] = (f"mailbox bits {w - 1} to 0 hold the {w} bits received, the "
                                               f"first bit received in bit {w - 1}"
                                               + ("; the spec sends the lsb first, so reverse them"
                                                  if s.order == "lsb" else ""))
    return notes


def image_json(sym, lanes, fclk, window=()):
    """lanes: [(lane, role, regs, pins, checks, notes)]; window: [(lane, role, Word)], the data
    window words the programs use, with the values the host loads before RUN."""
    from .window import DATA_WORDS
    out = {"protocol": sym.spec.name.text, "fclk_hz": int(fclk), "lanes": []}
    if window:
        out["data_window"] = {
            "words": DATA_WORDS,
            "use": [{"word": w.addr, "size": w.size, "lane": lane, "role": role, "what": w.name}
                    for lane, role, w in window],
            "load": [[w.addr, hexw(w.init)] for _, _, w in window if w.init is not None]}
    for lane, role, values, pins, checks, notes in lanes:
        out["lanes"].append({"lane": lane, "role": role.name.text,
                             "regs": {k: hexw(v) for k, v in sorted(values.items(), key=reg_order)},
                             "pins": pins, "checks": checks, "host": notes})
    return json.dumps(out, indent=2) + "\n"


def reg_order(kv):
    r = regs.LANE.get(kv[0])
    return (r["addr"] if r else 999, kv[0])
