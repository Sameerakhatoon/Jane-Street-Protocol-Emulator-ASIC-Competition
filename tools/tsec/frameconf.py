# SPDX-License-Identifier: Apache-2.0
"""Stage 6d, configuration of frame roles: the lane registers beside the program.

On top of the assist values (assists.py) a frame role needs:
  - LANE_CFG: TX and RX pins, the codec owning the TX pin, CRC feeding (TX or
    RX), bit order and RX word length, the line's idle level, side-set, the
    WAIT alternate;
  - CRC_INIT for a sender whose CRC starts after a constant prefix: the TX CRC
    sees every bit it sends, so the compiler runs the CRC register backwards
    over the prefix and loads the value that becomes the real init exactly
    where the CRC range starts;
  - CRC_CHECK as the RTL compares it: the register XOR CRC_XOR after a valid
    codeword and any bits the receiver lets in after it (one idle cell);
  - DPLL_SPT half a cycle before the sample point: a strobe fires in the first
    cycle at or past its threshold, so the earlier threshold centres it;
  - the response rules the lowering chose, in bit mode.
Every value goes through tools.tseasm.regs.pack.
"""
from . import assists
from ..tseasm import regs
from .config import hexw


def unstep(reg, bit, w, poly):
    """The CRC register one bit earlier (poly has its x^0 term, so the step is invertible)."""
    fb = reg & 1
    if fb:
        reg ^= poly
    reg >>= 1
    return reg | ((fb ^ bit) << (w - 1))


def crc_values(p):
    w = p.number("width", lo=5, hi=32)
    poly = p.number("poly", lo=0, hi=(1 << w) - 1)
    init = p.number("init", lo=0, hi=(1 << w) - 1)
    xorout = p.number("xorout", lo=0, hi=(1 << w) - 1)
    return w, poly, init, xorout, p.flag("refin"), p.flag("refout")


def lane_config(sym, res, fclk):
    """(regs, pins, checks, notes) of a compiled frame role."""
    att = res.chosen
    low = att.low
    role, lane = res.role, res.lane
    values, checks = assists.lane_config(sym, role, fclk, lambda w: low.vpin.get(w, 0))
    crc = low.assists.get("crc")
    if crc is not None:
        p = assists.Params(sym, crc, fclk)
        w, poly, init, xorout, refin, refout = crc_values(p)
        shift = 32 - w
        xo = assists.reflect(xorout, w) if refout else xorout
        if low.crc_prefix:
            reg = init
            for b in reversed(low.crc_prefix):
                reg = unstep(reg, b, w, poly)
            values["CRC_INIT"] = regs.pack("CRC_INIT", init=reg << shift)
            checks[f"{crc.name.text}.init_before_prefix"] = f"0x{reg:0{(w + 3) // 4}X} over " \
                                                           f"{len(low.crc_prefix)} prefix bits"
        if low.cfg.get("rx_crc"):
            res_raw = assists.residue(w, poly, init, refin, refout, xorout)
            reg = assists.crc_bits(low.crc_extra, w, poly, res_raw)
            values["CRC_CHECK"] = regs.pack("CRC_CHECK", check=(reg ^ xo) << shift)
            checks[f"{crc.name.text}.check_compared"] = (
                f"0x{reg ^ xo:0{(w + 3) // 4}X} (register XOR xorout after the codeword"
                + (f" and {len(low.crc_extra)} idle bit(s)" if low.crc_extra else "") + ")")
    if low.period is not None:
        if low.dpll_from_unit:
            values["DPLL_PERIOD"] = regs.pack("DPLL_PERIOD", period=low.q16)
            values["DPLL_SPT"] = values["DPLL_SJW"] = values["DPLL_CTL"] = 0
        elif getattr(low, "spt_reg", None) is not None:
            values["DPLL_SPT"] = regs.pack("DPLL_SPT", spt=low.spt_reg)
            checks["dpll.spt"] = f"{low.spt_reg}/16, half a cycle before the sample point"
    values["LANE_CFG"] = regs.pack("LANE_CFG", **low.cfg)
    for k, r in enumerate(low.rules):
        n = f"RULE{k}"
        values[f"{n}_MATCH"] = regs.pack(f"{n}_MATCH", pattern=r.match & 0xFFFF, mask=r.match >> 16)
        values[f"{n}_RESP_POS"] = regs.pack(f"{n}_RESP_POS", resp=r.resp, pos=r.pos, any=r.any)
        values[f"{n}_CTL"] = regs.pack(f"{n}_CTL", rlen_m1=r.rlen - 1, offset=r.offset, cond=r.cond,
                                       drive=r.drive, en=1)
    if low.rules:
        values["RULE_CLK"] = regs.pack("RULE_CLK", src=0)
    pins = {}
    for u in role.uses:
        wd = sym.wires[u.text]
        entry = {"dir": wd.dir, "vpin": low.vpin[u.text]}
        if wd.dir in ("in", "inout"):
            entry["vpin_in"] = low.vpin[u.text]
        if wd.dir in ("out", "inout"):
            entry["vpin_out"] = low.vpin[u.text]
            entry["PAD_OUT"] = hexw(regs.pack("PAD_OUT", src=lane * 4 + low.vpin[u.text],
                                              od=int(wd.open_drain), en=1, idle=low.idle_of(u.text),
                                              idle_oe=0 if wd.open_drain else 1))
        pins[u.text] = entry
    notes = dict(low.notes)
    for name, code in low.emit_codes.items():
        notes[f"emit {name}"] = f"event code {code}"
    for base, n in low.windows:
        checks["data_window"] = f"words {base} to {base + n - 1}"
    return values, pins, checks, notes
