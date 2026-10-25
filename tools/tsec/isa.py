"""ISA version 1 for the compiler: tables, encoder, decoder, disassembler.

THE SEAM. This is the only module of tsec that knows instruction encodings
or lane constants. The TABLES block below derives everything from the
generated ISA and register tables (isa/tse_isa.yaml and isa/tse_regs.yaml,
rendered into tools/tseasm by tools/gen_isa.py and tools/gen_regs.py), so an
ISA change reaches the compiler by regenerating those tables, not by editing
tsec. The self-test (selftest.py) pins the published sample encodings and
cycle counts, so a table change that moves a bit is caught at once.
"""
from ..tseasm import regs
from ..tseasm.isa_table import TABLE
from .diag import TseError


# ---------------------------------------------------------------- TABLES
def _enum_list(values):
    names = [n for n, _ in sorted(values.items(), key=lambda kv: kv[1])]
    assert [values[n] for n in names] == list(range(len(names))), "enum with holes"
    return names


def _operand(spec):
    kind = spec.get("enum") or ("minus1" if spec.get("kind") == "minus1" else None)
    return (spec["hi"], spec["lo"], kind) if kind else (spec["hi"], spec["lo"])


ENUMS = {name: _enum_list(v) for name, v in TABLE["enums"].items()}
SIDE_SET = TABLE["common"]["ss"]
# mnemonic: (mask, value, operand fields name: (hi, lo[, enum name or "minus1"]), constraints)
INSTRUCTIONS = {mn: (d["mask"], d["value"], {f: _operand(s) for f, s in d["fields"].items()},
                     d["constraints"])
                for mn, d in TABLE["instrs"].items()}
CLASS_L = {a for a in range(256) if regs.is_cfg(a)}     # a CSRW to one costs 3 cycles
_CMD = regs.LANE["CMD"]["fields"]
CMD_DPLL_ON, CMD_DPLL_OFF = 1 << _CMD["dpll_on"]["lo"], 1 << _CMD["dpll_off"]["lo"]
EV_BIT_START = TABLE["wait_idx"]["EV"]["BITSTART"]
TABLE_FLAG_HTXF = TABLE["wait_idx"]["FLAG"]["HTXF"]     # host TX mailbox full
FRAME_SLOTS = TABLE["frame_slots"]                      # SRAM slot frame S
FETCH_ENTRIES = TABLE["fetch_entries"]                  # fetch buffer words per lane
FETCH_LATENCY = TABLE["fetch_latency"]
DATA_SLOT_PERIOD = TABLE["data_frame"]                  # lane k's data slot: s mod 16 == 4k + 3
VPINS = 4                                               # virtual pins per lane
EDGE_TO_PORT = TABLE["latency"]["edge_to_port"]         # pin-edge rule: seen in e, port at e + td + this
RULE_HOLD = "hold" in regs.LANE["RULE_CLK"]["fields"]   # a rule drive kept until the program writes txpin
RULE_RESP_POS = regs.LANE["RULE0_RESP_POS"]["addr"]
_target = INSTRUCTIONS["JMP"][2]["target"]
MAX_INSTRUCTIONS = 1 << (_target[0] - _target[1] + 1)
_d = INSTRUCTIONS["DLY"][2]["d"]
DLY_MAX = (1 << (_d[0] - _d[1] + 1)) - 1
# ---------------------------------------------------------------- end of TABLES


def _bits(w, hi, lo):
    return (w >> lo) & ((1 << (hi - lo + 1)) - 1)


def _mask(hi, lo):
    return ((1 << (hi - lo + 1)) - 1) << lo


def _field(spec):
    return spec[0], spec[1], spec[2] if len(spec) > 2 else None


def _violates(constraints, f):
    return any(all(f.get(k, 0) == v for k, v in c["if"].items())
               and any(f.get(k, 0) != v for k, v in c["then"].items()) for c in constraints)


def encode(mn, f):
    _, value, fields, constraints = INSTRUCTIONS[mn]
    w = value | f.get("ss", 0) << SIDE_SET[1]
    for name, spec in fields.items():
        hi, lo, kind = _field(spec)
        v = f.get(name, 1 if kind == "minus1" else (ENUMS[kind][0] if kind else 0))
        raw = v - 1 if kind == "minus1" else (ENUMS[kind].index(v) if kind else v)
        if not 0 <= raw < 1 << (hi - lo + 1):
            raise TseError("R302", f"{mn} {name} = {v} does not fit bits [{hi}:{lo}]")
        w |= raw << lo
    if _violates(constraints, f):
        raise TseError("R302", f"{mn}: illegal field combination {f}")
    return w


def decode(w):
    """(mnemonic, fields) or None for an illegal word (reserved bit set or enum hole)."""
    for mn, (mask, value, fields, constraints) in INSTRUCTIONS.items():
        if w & mask != value:
            continue
        used = mask | _mask(*SIDE_SET)
        f = {"ss": _bits(w, *SIDE_SET)}
        for name, spec in fields.items():
            hi, lo, kind = _field(spec)
            used |= _mask(hi, lo)
            v = _bits(w, hi, lo)
            if kind == "minus1":
                v += 1
            elif kind:
                if v >= len(ENUMS[kind]):
                    return None
                v = ENUMS[kind][v]
            f[name] = v
        if w & ~used & 0xFFFF or _violates(constraints, f):
            return None
        return mn, f
    return None


def fmt(mn, f, names=None):
    """Assembler text of one instruction; names maps jump targets to labels."""
    names = names or {}
    g = f.get
    d = f" [{g('delay')}]" if g("delay") else ""
    if mn == "JMP":
        s = f"{g('cond')}, {names.get(g('target'), g('target'))}"
    elif mn == "WAIT":
        s = f"{g('pol', 0)}, {g('src', 'PIN')}, {g('idx', 0)}" + (", to" if g("to") else "") \
            + (", alt" if g("alt") else "") + d
    elif mn == "SET" and g("dst") in ("PINS", "PINDIRS"):
        s = f"{g('dst')}, {g('imm') & 15}, mask=0b{g('imm') >> 4:04b}"
    elif mn == "SET":
        s = f"{g('dst')}, " + (f"0x{g('imm'):02X}" if g("dst") == "CMD" else str(g("imm")))
    elif mn == "MOV":
        s = f"{g('dst')}, {g('src')}{d}"
    elif mn in ("OUT", "IN"):
        s = f"{g('dst') if mn == 'OUT' else g('src')}, {g('n', 1)}{d}"
    elif mn in ("PUSH", "PULL"):
        s = "block" if g("blk") else ""
    elif mn in ("CSRW", "CSRR"):
        s = f"0x{g('addr'):02X}"
    elif mn in ("LDW", "STW"):
        s = "X" if g("ind") else str(g("addr", 0))
    elif mn == "DLY":
        s = str(g("d"))
    elif mn == "TMR":
        s = ", ".join((["cclr"] if g("cclr") else []) + ([f"gsel={g('gsel')}"] if g("gwe") else [])) + d
    elif mn == "SIG":
        s = f"0b{g('mask'):04b}, 0b{g('val'):04b}"
    else:
        s = f"{g('code')}{d}"
    if g("ss"):
        s += f" side {g('ss')}"
    return f"{mn:<5} {s}".rstrip()
