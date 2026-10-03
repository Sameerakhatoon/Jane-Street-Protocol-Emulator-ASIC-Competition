"""Reference trace of one lane, built from the ISA cost model instead of cycle counters.

Dispatch times come from tools.tseasm.isa: cost() for the static part,
dslot_cycles() for LDW and STW, first_slot() for the fetch of a missed word,
plus the two-word buffer rule. Control state follows the instruction
semantics. The result is expanded to one row per cycle with the same fields
the testbench writes for src/tse_seq_spec.sv.
"""
from typing import NamedTuple

from tools.tseasm import isa, regs

FIELDS = ("issue", "pc", "fetch", "fword", "halted", "x", "y", "t", "c", "tflag", "aflag", "cfg_we",
          "cfg_addr", "cfg_wdata")
PULSES = ("issue", "fetch", "cfg_we")


class Env(NamedTuple):
    pins: int
    ev: int
    flag: int
    chip: int
    hrx_full: int
    htx_full: int
    dval: int

    @staticmethod
    def unpack(v):
        return Env(v & 0xF, (v >> 4) & 0xFF, (v >> 12) & 0xFF, (v >> 20) & 0xFF,
                   (v >> 28) & 1, (v >> 29) & 1, v >> 32)

    def pack(self):
        return (self.pins | self.ev << 4 | self.flag << 12 | self.chip << 20
                | self.hrx_full << 28 | self.htx_full << 29 | self.dval << 32)


class Track:
    """A value per cycle, recorded as changes: set(k, v) holds from cycle k on."""

    def __init__(self, v):
        self.chg = {0: v}

    def set(self, k, v):
        self.chg[k] = v

    def expand(self, n):
        out, v = [], self.chg[0]
        for k in range(n):
            v = self.chg.get(k, v)
            out.append(v)
        return out


def _bits(v, f):
    return (v >> f["lo"]) & ((1 << (f["hi"] - f["lo"] + 1)) - 1)


def _cfg(name, v):
    return regs.unpack(name, v & regs.LANE[name]["mask"])


def run(prog, env, lane, lat, start_pc, cfg_lane, guard01, guard23, ncyc):
    """Per-cycle rows (dicts of FIELDS) for ncyc cycles; env is a list of Env, one per cycle."""
    tr = {f: Track(0) for f in FIELDS if f not in PULSES and f != "c"}
    pulses = {f: {} for f in PULSES}
    tr["pc"].set(0, start_pc)
    lcfg = _cfg("LANE_CFG", cfg_lane)
    g01, g23 = _cfg("GUARD01", guard01), _cfg("GUARD23", guard23)
    gts = [g01["gt0"], g01["gt1"], g23["gt2"], g23["gt3"]]
    x = y = t = gsel = tflag = aflag = 0
    clears = [0]                        # cycles from which C counts up from 0

    def cval(k):
        return min(k - clears[-1], 0xFFFF)

    pc = start_pc
    s0 = isa.first_slot(0, lane)
    pulses["fetch"][s0] = start_pc >> 1
    tags = [start_pc >> 1]
    nxt = s0 + lat
    pending = []                        # (visible from cycle, register, value)
    while nxt < ncyc:
        c = nxt
        for p in [p for p in pending if p[0] <= c]:
            pending.remove(p)
            if p[1] == "LANE_CFG":
                lcfg = _cfg("LANE_CFG", p[2])
            else:
                g = _cfg(p[1], p[2])
                if p[1] == "GUARD01":
                    gts[0:2] = [g["gt0"], g["gt1"]]
                else:
                    gts[2:4] = [g["gt2"], g["gt3"]]
        ins = isa.decode(prog[pc])
        if ins is None:
            tr["halted"].set(c + 1, 1)
            break
        pulses["issue"][c] = 1
        e = env[c]
        f, name = ins.fields, ins.name
        cnow = cval(c)
        npc = (pc + 1) & 0xFF
        nx, ny, nt = x, y, t
        mvals = [x, y, e.dval & 0xFFFF, e.dval & 0xFFFF, e.dval & 0xFFFF, t, ~x & 0xFFFF, cnow]
        if name == "JMP":
            cond = f["cond"]
            taken = [True, x == 0, x != 0, y != 0, x != y, (e.pins >> lcfg["jmp_pin"]) & 1 == 1,
                     bool(tflag or aflag), cnow >= gts[gsel]][cond]
            if cond == isa.ENUMS["cond"]["XDEC"]:
                nx = (x - 1) & 0xFFFF
            if cond == isa.ENUMS["cond"]["YDEC"]:
                ny = (y - 1) & 0xFFFF
            if taken:
                npc = f["target"]
        elif name == "SET":
            dst, imm = f["dst"], f["imm"]
            e_ = isa.ENUMS["setdst"]
            if dst == e_["X"]:
                nx = imm
            elif dst == e_["Y"]:
                ny = imm
            elif dst == e_["T"]:
                nt = ((t & 0xFF) << 8) | imm
            elif dst == e_["XH"]:
                nx = (x & 0xFF) | imm << 8
        elif name == "MOV":
            v = mvals[f["src"]]
            e_ = isa.ENUMS["movdst"]
            if f["dst"] == e_["X"]:
                nx = v
            elif f["dst"] == e_["Y"]:
                ny = v
            elif f["dst"] == e_["T"]:
                nt = v
            elif f["dst"] == e_["PC"]:
                npc = v & 0xFF
        elif name == "OUT":
            v = e.dval & 0xFFFF
            if f["n"] < 16:
                v &= (1 << f["n"]) - 1
            e_ = isa.ENUMS["outdst"]
            if f["dst"] == e_["X"]:
                nx = v
            elif f["dst"] == e_["Y"]:
                ny = v
            elif f["dst"] == e_["T"]:
                nt = v
        elif name == "CSRR":
            nx, ny = e.dval & 0xFFFF, e.dval >> 16
        elif name == "TMR":
            if f["cclr"]:
                clears.append(c + 1)
            if f["gwe"]:
                gsel = f["gsel"]
        elif name == "CSRW" and regs.is_cfg(f["addr"]):
            pulses["cfg_we"][c] = (f["addr"], y << 16 | x)
            reg = regs.lane_reg(f["addr"])
            if reg in ("LANE_CFG", "GUARD01", "GUARD23"):
                pending.append((c + isa.LATENCY["cfg_write_visible"], reg, y << 16 | x))

        co = isa.cost(ins)
        if co.dslot:
            d = isa.dslot_cycles(co.dslot, c, lane, lat)
        elif co.wait:
            end, reason = _wait_end(ins, env, c, ncyc, t, lcfg, lambda k: cval(k) >= gts[gsel])
            d = co.base + (end - c)
            if name == "WAIT":
                tr["tflag"].set(c + 1, 0)
                tr["aflag"].set(c + 1, 0)
                tflag, aflag = int(reason == "to"), int(reason == "alt")
                tr["tflag"].set(end + 1, tflag)
                tr["aflag"].set(end + 1, aflag)
        else:
            d = co.base
        for k, v in (("x", nx), ("y", ny), ("t", nt)):
            tr[k].set(c + 1, v)
        x, y, t = nx, ny, nt

        w = npc >> 1
        if w in tags:
            tags.remove(w)
            nxt = c + d
        else:
            s = isa.first_slot(c, lane)
            pulses["fetch"][s] = w
            tags = tags[:isa.FETCH_ENTRIES - 1]
            nxt = max(c + d, s + lat)
        tags.insert(0, w)
        tr["pc"].set(c + 1, npc)
        pc = npc

    cols = {k: v.expand(ncyc) for k, v in tr.items()}
    cs, ci = [], 0
    for k in range(ncyc):
        while ci + 1 < len(clears) and clears[ci + 1] <= k:
            ci += 1
        cs.append(min(k - clears[ci], 0xFFFF))
    rows = []
    for k in range(ncyc):
        r = {f: cols[f][k] for f in cols}
        r["c"] = cs[k]
        r["issue"] = pulses["issue"].get(k, 0)
        r["cfg_we"] = int(k in pulses["cfg_we"])
        r["cfg_addr"], r["cfg_wdata"] = pulses["cfg_we"].get(k, (None, None))
        r["fetch"] = int(k in pulses["fetch"])
        r["fword"] = pulses["fetch"].get(k)
        rows.append(r)
    return rows


def _sig(src, idx, e, chg, guard):
    if src == 0:
        return (chg >> (idx - 4)) & 1 if idx >= 4 else (e.pins >> idx) & 1
    if src == 1:
        return (e.ev >> idx) & 1
    if src == 2:
        return int(guard) if idx == 0 else 1 if idx == 7 else (e.flag >> idx) & 1
    return 0 if idx > 5 else (e.chip >> idx) & 1


def _wait_end(ins, env, c, ncyc, t, lcfg, guard_at):
    """(cycle, reason) in which a waiting instruction dispatched in c sees its end; (ncyc, None)
    when that is not within the trace (later rows then never see it end)."""
    f = ins.fields
    if ins.name == "PUSH":
        return next(((j, "own") for j in range(c, ncyc) if not env[j].hrx_full), (ncyc, None))
    if ins.name == "PULL":
        return next(((j, "own") for j in range(c, ncyc) if env[j].htx_full), (ncyc, None))
    snap, chg, tleft = env[c].pins, 0, t
    for j in range(c, ncyc):
        e = env[j]
        if j > c:
            chg |= e.pins ^ snap
        g = guard_at(j)
        if _sig(f["src"], f["idx"], e, chg, g) == f["pol"]:
            return j, "own"
        if f["alt"] and _sig(lcfg["alt_src"], lcfg["alt_idx"], e, chg, g) == lcfg["alt_pol"]:
            return j, "alt"
        if f["to"] and tleft == 0:
            return j, "to"
        tleft = (tleft - 1) & 0xFFFF
    return ncyc, None
