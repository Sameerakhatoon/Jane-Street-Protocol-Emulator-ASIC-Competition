"""Random legal programs, input waveforms and start configurations for the sequencer spec test."""
import random

from tools.tseasm import isa, regs

from seqspec_ref import Env

# instruction mix: control and waiting heavy, every instruction present
WEIGHTS = {"JMP": 14, "WAIT": 12, "SET": 10, "MOV": 8, "OUT": 5, "IN": 4, "PUSH": 4, "PULL": 4,
           "CSRW": 12, "STW": 4, "CSRR": 4, "LDW": 4, "DLY": 6, "TMR": 6, "SIG": 2, "EMIT": 3}
CFG_ADDRS = sorted(r["addr"] for r in regs.LANE.values() if r["class"] == "L")
SEQ_CFG = [regs.LANE[n]["addr"] for n in ("LANE_CFG", "GUARD01", "GUARD23")]
OTHER_ADDRS = sorted(r["addr"] for r in regs.LANE.values() if r["class"] != "L")


def _field(rng, name, fname, f):
    lo, hi = isa.field_range(name, fname)
    if "enum" in f:
        return rng.choice(sorted(set(isa.ENUMS[f["enum"]].values())))
    if fname in ("delay", "d") and rng.random() < 0.85:
        return rng.randint(lo, min(hi, 6))
    return rng.randint(lo, hi)


def rand_instr(rng):
    name = rng.choices(list(WEIGHTS), list(WEIGHTS.values()))[0]
    t = isa.INSTRS[name]
    vals = {k: _field(rng, name, k, f) for k, f in t["fields"].items() if k not in t["match"]}
    vals["ss"] = rng.randint(0, 3)
    if name in ("LDW", "STW") and vals["ind"]:
        vals["addr"] = 0
    if name == "CSRW":
        r = rng.random()
        vals["addr"] = (rng.choice(SEQ_CFG) if r < 0.25 else rng.choice(CFG_ADDRS) if r < 0.65
                        else rng.choice(OTHER_ADDRS) if r < 0.85 else rng.randint(0, 255))
    if name == "WAIT" and vals["src"] == 3 and vals["idx"] > 5 and vals["pol"] and not vals["to"]:
        vals["pol"] = 0                 # a reserved chip flag never reads 1
    if name == "SET" and vals["dst"] == isa.ENUMS["setdst"]["T"] and rng.random() < 0.7:
        vals["imm"] = rng.randint(0, 12)
    return isa.encode(name, **vals)


def rand_prog(rng, illegal=0.004):
    words = []
    for _ in range(256):
        if rng.random() < illegal:
            w = rng.randint(0, 0xFFFF)
            while isa.is_legal(w):
                w = rng.randint(0, 0xFFFF)
            words.append(w)
        else:
            words.append(rand_instr(rng))
    return words


def rand_env(rng, ncyc):
    p_pin = rng.choice([0.02, 0.1, 0.3])
    p_bit = rng.choice([0.2, 0.5, 0.8])
    p_mb = rng.choice([0.3, 0.7])
    pins, out = rng.randint(0, 15), []
    for _ in range(ncyc):
        for b in range(4):
            if rng.random() < p_pin:
                pins ^= 1 << b
        bits = [sum((rng.random() < p_bit) << i for i in range(8)) for _ in range(3)]
        dval = rng.randint(0, 63) if rng.random() < 0.7 else rng.randint(0, 0xFFFFFFFF)
        out.append(Env(pins, bits[0], bits[1], bits[2], int(rng.random() < p_mb),
                       int(rng.random() < p_mb), dval))
    return out


def rand_cfg(rng):
    def gt():
        return rng.randint(0, 40) if rng.random() < 0.8 else rng.randint(0, 0xFFFF)
    return (rng.randint(0, 255), rng.randint(0, 0xFFFFFFFF), gt() | gt() << 16, gt() | gt() << 16)


def write_test(d, n, prog, env, cfg):
    (d / f"t{n}.prog").write_text("".join(f"{w:04x}\n" for w in prog))
    (d / f"t{n}.env").write_text("".join(f"{e.pack():016x}\n" for e in env))
    (d / f"t{n}.cfg").write_text("".join(f"{v:08x}\n" for v in cfg))
