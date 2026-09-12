#!/usr/bin/env python3
"""Generate the SystemVerilog ISA package and the Python ISA table from isa/tse_isa.yaml.

The table is checked before anything is written. It stops with a message on a
field that leaves the word or overlaps another, a match or enum value that does
not fit its field, two instructions that can decode the same word, an event
name that does not fit idx, and a constraint or cycle formula that names an
unknown field.

usage: gen_isa.py isa/tse_isa.yaml src/tse_isa_pkg.sv tools/tseasm/isa_table.py
"""
import pprint
import sys

import yaml

HEADER_KEYS = ("isa", "version", "word_bits", "frame_slots", "fetch_entries",
               "fetch_latency", "data_frame")
FIELD_KEYS = {"bits", "enum", "kind", "flag", "key", "set_by", "token", "enum_by", "asm_max", "doc"}
INSTR_KEYS = {"match", "fields", "cycles", "constraints", "doc"}


class GenError(Exception):
    pass


def mask_of(hi, lo):
    return ((1 << (hi - lo + 1)) - 1) << lo


def check_field(where, f, width):
    bad = set(f) - FIELD_KEYS
    if bad:
        raise GenError(f"{where}: unknown keys {sorted(bad)}")
    hi, lo = f["bits"]
    if not (0 <= lo <= hi < width):
        raise GenError(f"{where}: bits [{hi}:{lo}] outside the {width}-bit word")
    return hi, lo


def check_enums(enums, instrs):
    for name, vals in enums.items():
        if not all(isinstance(k, str) for k in vals):
            raise GenError(f"enum {name}: a name is not a string (quote YAML words like NULL)")
        if len(set(vals.values())) != len(vals):
            raise GenError(f"enum {name}: two names share a value")
        widths = {f["bits"][0] - f["bits"][1] for i in instrs.values()
                  for f in i.get("fields", {}).values() if f.get("enum") == name}
        if len(widths) != 1:
            raise GenError(f"enum {name}: must be used by fields of one width")


def build_instr(name, ins, common, enums, width):
    bad = set(ins) - INSTR_KEYS
    if bad:
        raise GenError(f"{name}: unknown keys {sorted(bad)}")
    fields = {}
    used = 0
    for fname, f in {**common, **ins.get("fields", {})}.items():
        hi, lo = check_field(f"{name}.{fname}", f, width)
        m = mask_of(hi, lo)
        if used & m:
            raise GenError(f"{name}: field {fname} overlaps another field")
        used |= m
        if "enum" in f:
            if f["enum"] not in enums:
                raise GenError(f"{name}.{fname}: unknown enum {f['enum']}")
            if max(enums[f["enum"]].values()) >= 1 << (hi - lo + 1):
                raise GenError(f"{name}.{fname}: enum {f['enum']} does not fit [{hi}:{lo}]")
        fields[fname] = dict(f, hi=hi, lo=lo)
        del fields[fname]["bits"]
    mask = value = 0
    for fname, v in ins["match"].items():
        if fname not in fields:
            raise GenError(f"{name}: match on unknown field {fname}")
        f = fields[fname]
        if not 0 <= v < 1 << (f["hi"] - f["lo"] + 1):
            raise GenError(f"{name}: match {fname}={v} does not fit [{f['hi']}:{f['lo']}]")
        mask |= mask_of(f["hi"], f["lo"])
        value |= v << f["lo"]
    reserved = ((1 << width) - 1) & ~used
    for c in ins.get("constraints", []):
        for fname in list(c["if"]) + list(c["then"]):
            if fname not in fields:
                raise GenError(f"{name}: constraint names unknown field {fname}")
    for k, v in ins["cycles"].items():
        if k in ("delay", "wait") and isinstance(v, str) and v not in fields:
            raise GenError(f"{name}: cycles.{k} names unknown field {v}")
    for fname, f in fields.items():
        for ref in ("set_by", "enum_by"):
            if ref in f and f[ref] not in fields:
                raise GenError(f"{name}.{fname}: {ref} names unknown field {f[ref]}")
    operands = {k: v for k, v in fields.items() if k not in ins["match"] and k not in common}
    return {
        "mask": mask | reserved,
        "value": value,
        "reserved": reserved,
        "match": dict(ins["match"]),
        "fields": operands,
        "cycles": dict(ins["cycles"]),
        "constraints": [dict(c) for c in ins.get("constraints", [])],
        "doc": ins.get("doc", ""),
    }


def load(path):
    with open(path) as fh:
        src = yaml.safe_load(fh)
    width = src["word_bits"]
    check_enums(src["enums"], src["instructions"])
    common = src["common"]
    instrs = {n: build_instr(n, i, common, src["enums"], width)
              for n, i in src["instructions"].items()}
    names = list(instrs)
    for i, a in enumerate(names):            # no word may decode to two instructions
        for b in names[i + 1:]:
            m = instrs[a]["mask"] & instrs[b]["mask"]
            if (instrs[a]["value"] ^ instrs[b]["value"]) & m == 0:
                raise GenError(f"encodings of {a} and {b} overlap")
    idx_bits = instrs["WAIT"]["fields"]["idx"]
    for group, names_ in src["wait_idx"].items():
        if group not in src["enums"]["wsrc"]:
            raise GenError(f"wait_idx: unknown source group {group}")
        if max(names_.values()) >= 1 << (idx_bits["hi"] - idx_bits["lo"] + 1):
            raise GenError(f"wait_idx {group}: index does not fit idx")
    table = {k: src[k] for k in HEADER_KEYS}
    table.update(latency=dict(src["latency"]),
                 common={k: (v["bits"][0], v["bits"][1]) for k, v in common.items()},
                 enums={k: dict(v) for k, v in src["enums"].items()},
                 wait_idx={k: dict(v) for k, v in src["wait_idx"].items()},
                 instrs=instrs)
    return table


def bits_str(mask, value, width):
    return "".join("?" if not (mask >> i) & 1 else str((value >> i) & 1)
                   for i in range(width - 1, -1, -1))


def sv_field(w, f):
    return f"{w}[{f['hi']}:{f['lo']}]"


def sv_const(width, v):
    return f"{width}'d{v}"


def legal_terms(name, t, enums):
    """SystemVerilog conditions, besides the reserved bits, for a decoded word to be legal."""
    terms = []
    for fname, f in t["fields"].items():
        if "enum" not in f:
            continue
        width = f["hi"] - f["lo"] + 1
        vals = sorted(set(enums[f["enum"]].values()))
        if len(vals) < 1 << width:
            terms.append("(" + " || ".join(f"{sv_field('w', f)} == {sv_const(width, v)}"
                                           for v in vals) + ")")
    for c in t["constraints"]:
        cond = " && ".join(f"{sv_field('w', t['fields'][k])} == "
                           f"{sv_const(t['fields'][k]['hi'] - t['fields'][k]['lo'] + 1, v)}"
                           for k, v in c["if"].items())
        then = " && ".join(f"{sv_field('w', t['fields'][k])} == "
                           f"{sv_const(t['fields'][k]['hi'] - t['fields'][k]['lo'] + 1, v)}"
                           for k, v in c["then"].items())
        terms.append(f"(!({cond}) || ({then}))")
    return terms


def emit_sv(tab, src_name):
    w = tab["word_bits"]
    names = list(tab["instrs"])
    out = [f"// generated by tools/gen_isa.py from {src_name}, do not edit",
           "/* verilator lint_off UNUSEDPARAM */",
           "package tse_isa_pkg;",
           f"  localparam int ISA_VERSION   = {tab['version']};",
           f"  localparam int WORD_BITS     = {w};",
           f"  localparam int FRAME_SLOTS   = {tab['frame_slots']};",
           f"  localparam int FETCH_ENTRIES = {tab['fetch_entries']};",
           f"  localparam int FETCH_LATENCY = {tab['fetch_latency']};",
           f"  localparam int DATA_FRAME    = {tab['data_frame']};"]
    out += [f"  localparam int LAT_{k.upper()} = {v};" for k, v in tab["latency"].items()]
    out += ["", "  typedef enum logic [4:0] {"]
    out += [f"    I_{n}," for n in names] + ["    I_ILLEGAL", "  } iclass_t;", ""]
    for k, (hi, lo) in tab["common"].items():
        out.append(f"  localparam int {k.upper()}_HI = {hi}, {k.upper()}_LO = {lo};")
    enum_width = {f["enum"]: f["hi"] - f["lo"] + 1
                  for t in tab["instrs"].values() for f in t["fields"].values() if "enum" in f}
    for ename, vals in tab["enums"].items():
        width = enum_width[ename]
        for vname, v in vals.items():
            out.append(f"  localparam logic [{width - 1}:0] {ename.upper()}_{vname} = {sv_const(width, v)};")
    for group, vals in tab["wait_idx"].items():
        for vname, v in vals.items():
            out.append(f"  localparam logic [2:0] WIDX_{group}_{vname} = 3'd{v};")
    out.append("")
    for n, t in tab["instrs"].items():
        out.append(f"  localparam logic [{w - 1}:0] {n}_MASK = {w}'h{t['mask']:04X}, "
                   f"{n}_MATCH = {w}'h{t['value']:04X};")
        for fname, f in t["fields"].items():
            out.append(f"  localparam int {n}_{fname.upper()}_HI = {f['hi']}, "
                       f"{n}_{fname.upper()}_LO = {f['lo']};")
        for k, v in t["cycles"].items():
            if isinstance(v, int) and not isinstance(v, bool):
                out.append(f"  localparam int {n}_CYC_{k.upper()} = {v};")
    out += ["",
            "  // Reserved bits are fixed zeros in each pattern, so a corrupted word is I_ILLEGAL.",
            f"  function automatic iclass_t tse_decode(input logic [{w - 1}:0] w);",
            "    casez (w)"]
    out += [f"      {w}'b{bits_str(t['mask'], t['value'], w)}: return I_{n};"
            for n, t in tab["instrs"].items()]
    out += ["      default: return I_ILLEGAL;", "    endcase", "  endfunction", "",
            "  // tse_decode plus enumeration holes and field constraints.",
            f"  function automatic logic tse_legal(input logic [{w - 1}:0] w);",
            "    case (tse_decode(w))",
            "      I_ILLEGAL: return 1'b0;"]
    for n, t in tab["instrs"].items():
        terms = legal_terms(n, t, tab["enums"])
        if terms:
            out.append(f"      I_{n}: return " + " && ".join(terms) + ";")
    out += ["      default: return 1'b1;", "    endcase", "  endfunction",
            "endpackage", "/* verilator lint_on UNUSEDPARAM */"]
    return "\n".join(out) + "\n"


def emit_py(tab, src_name):
    return (f"# generated by tools/gen_isa.py from {src_name}, do not edit\n"
            "# The checked ISA table; tools/tseasm/isa.py is the API on top of it.\n"
            f"TABLE = {pprint.pformat(tab, width=100, sort_dicts=False)}\n")


def main(argv):
    if len(argv) != 4:
        sys.exit(__doc__.strip().splitlines()[-1])
    try:
        tab = load(argv[1])
    except GenError as e:
        sys.exit(f"gen_isa: {argv[1]}: {e}")
    with open(argv[2], "w") as fh:
        fh.write(emit_sv(tab, argv[1]))
    with open(argv[3], "w") as fh:
        fh.write(emit_py(tab, argv[1]))
    print(f"gen_isa: {len(tab['instrs'])} instructions, no overlapping encodings")


if __name__ == "__main__":
    main(sys.argv)
