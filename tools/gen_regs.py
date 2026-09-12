#!/usr/bin/env python3
"""Generate the SystemVerilog register package and the Python register table from isa/tse_regs.yaml.

The table is checked before anything is written. It stops with a message on
two registers at one address (arrays expanded), a register outside the lane
window or outside every host region, overlapping regions, a field that leaves
the 32-bit word or overlaps another, a limit or enum that does not fit its
field, a require rule on an unknown field, and more class-L lane registers
than the 32 words of a configuration bank.

Class-L lane registers get bank words 0, 1, 2, ... in address order: the
configuration bank is addressed by word (5 bits), the lane by register
address (8 bits), and lane_reg_word() maps one to the other.

usage: gen_regs.py isa/tse_regs.yaml src/tse_regs_pkg.sv tools/tseasm/regs_table.py
"""
import pprint
import sys

import yaml

REG_KEYS = {"addr", "class", "fields", "doc", "count", "stride", "host", "require"}
FIELD_KEYS = {"bits", "min", "max", "enum"}
BANK_WORDS = 32
CLASS_CODE = {"L": "RC_L", "F": "RC_F", "P": "RC_P", "R": "RC_R"}


class GenError(Exception):
    pass


def field_spec(where, f):
    if isinstance(f, list):
        f = {"bits": f}
    bad = set(f) - FIELD_KEYS
    if bad:
        raise GenError(f"{where}: unknown keys {sorted(bad)}")
    hi, lo = f["bits"]
    if not 0 <= lo <= hi <= 31:
        raise GenError(f"{where}: bits [{hi}:{lo}] outside the 32-bit word")
    top = (1 << (hi - lo + 1)) - 1
    out = {"hi": hi, "lo": lo}
    for k in ("min", "max"):
        if k in f:
            if not 0 <= f[k] <= top:
                raise GenError(f"{where}: {k} {f[k]} does not fit [{hi}:{lo}]")
            out[k] = f[k]
    if "enum" in f:
        if max(f["enum"].values()) > out.get("max", top):
            raise GenError(f"{where}: enum value does not fit")
        out["enum"] = dict(f["enum"])
    return out


def build_reg(name, r, classes):
    bad = set(r) - REG_KEYS
    if bad:
        raise GenError(f"{name}: unknown keys {sorted(bad)}")
    if r["class"] not in classes:
        raise GenError(f"{name}: unknown class {r['class']}")
    fields, used = {}, 0
    for fname, f in r["fields"].items():
        spec = field_spec(f"{name}.{fname}", f)
        m = ((1 << (spec["hi"] - spec["lo"] + 1)) - 1) << spec["lo"]
        if used & m:
            raise GenError(f"{name}: field {fname} overlaps another field")
        used |= m
        fields[fname] = spec
    for rule in r.get("require", []):
        for fname in list(rule["if"]) + list(rule["then"]):
            if fname not in fields:
                raise GenError(f"{name}: require names unknown field {fname}")
    cls = classes[r["class"]]
    return {
        "addr": r["addr"],
        "count": r.get("count", 1),
        "stride": r.get("stride", 1),
        "class": r["class"],
        "storage": cls["storage"],
        "reset": cls.get("reset"),
        "cleared_by": cls.get("cleared_by"),
        "lane_access": cls["lane"],
        "host_access": r.get("host", cls["host"]),
        "width": max(f["hi"] for f in fields.values()) + 1,
        "mask": used,
        "fields": fields,
        "require": [dict(x) for x in r.get("require", [])],
        "doc": r.get("doc", ""),
    }


def expand(regs):
    for name, r in regs.items():
        for i in range(r["count"]):
            yield name, r["addr"] + i * r["stride"]


def check_unique(where, regs):
    seen = {}
    for name, a in expand(regs):
        if a in seen:
            raise GenError(f"{where}: {name} and {seen[a]} collide at 0x{a:04X}")
        seen[a] = name


def load(path):
    with open(path) as fh:
        src = yaml.safe_load(fh)
    classes = src["classes"]
    lane = {n: build_reg(n, r, classes) for n, r in src["lane_regs"].items()}
    chip = {n: build_reg(n, r, classes) for n, r in src["chip_regs"].items()}
    win = src["lane_window"]
    for name, a in expand(lane):
        if not 0 <= a < win["size"]:
            raise GenError(f"lane register {name} at 0x{a:X} is outside the lane window")
    check_unique("lane registers", lane)
    check_unique("chip registers", chip)
    regions = sorted(src["regions"].items(), key=lambda kv: kv[1]["base"])
    for (na, ra), (nb, rb) in zip(regions, regions[1:]):
        if ra["base"] + ra["size"] > rb["base"]:
            raise GenError(f"regions {na} and {nb} overlap")
    lanes_region = src["regions"]["lanes"]
    if (lanes_region["base"], lanes_region["size"]) != (win["host_base"], win["stride"] * win["max_lanes"]):
        raise GenError("the lanes region does not match lane_window")
    for name, a in expand(chip):
        inside = [n for n, r in regions if r["base"] <= a < r["base"] + r["size"]]
        if not inside or inside[0] in ("sram", "data", "fallback", "lanes"):
            raise GenError(f"chip register {name} at 0x{a:04X} is not in a register region")
    word = 0
    for name in sorted(lane, key=lambda n: lane[n]["addr"]):
        if lane[name]["class"] == "L":
            lane[name]["word"] = word
            word += 1
    if word > BANK_WORDS:
        raise GenError(f"{word} class-L lane registers do not fit a {BANK_WORDS}-word bank")
    return {
        "version": src["version"],
        "classes": {k: dict(v) for k, v in classes.items()},
        "lane_window": dict(win),
        "regions": {k: dict(v) for k, v in src["regions"].items()},
        "lane_bank_words": word,
        "lane": lane,
        "chip": chip,
    }


def emit_fields(out, prefix, r):
    for fname, f in r["fields"].items():
        out.append(f"  localparam int {prefix}_{fname.upper()}_HI = {f['hi']}, "
                   f"{prefix}_{fname.upper()}_LO = {f['lo']};")


def emit_sv(tab, src_name):
    lane, chip = tab["lane"], tab["chip"]
    win = tab["lane_window"]
    out = [f"// generated by tools/gen_regs.py from {src_name}, do not edit",
           "/* verilator lint_off UNUSEDPARAM */",
           "package tse_regs_pkg;",
           "  typedef enum logic [2:0] {RC_NONE, RC_L, RC_F, RC_P, RC_R} rclass_t;",
           f"  localparam logic [15:0] LANE_HOST_BASE = 16'h{win['host_base']:04X}, "
           f"LANE_HOST_STRIDE = 16'h{win['stride']:04X};"]
    for n, r in tab["regions"].items():
        out.append(f"  localparam logic [15:0] REGION_{n.upper()}_BASE = 16'h{r['base']:04X}, "
                   f"REGION_{n.upper()}_SIZE = 16'h{r['size']:04X};")
    out += ["", "  // lane registers (CSRW/CSRR address, host 0x1000 + 0x100 x lane + address)"]
    for n, r in lane.items():
        out.append(f"  localparam logic [7:0] LR_{n} = 8'h{r['addr']:02X};")
        out.append(f"  localparam logic [31:0] LR_{n}_MASK = 32'h{r['mask']:08X};")
        if "word" in r:
            out.append(f"  localparam logic [4:0] LW_{n} = 5'd{r['word']};")
        emit_fields(out, f"LR_{n}", r)
    masks = [0] * BANK_WORDS
    for r in lane.values():
        if "word" in r:
            masks[r["word"]] = r["mask"]
    out += ["",
            f"  localparam int LANE_BANK_WORDS = {tab['lane_bank_words']};",
            "  // bits that exist in each bank word, word 31 first (the bank's MASK parameter)",
            "  localparam logic [1023:0] LANE_BANK_MASK = {"]
    out += [f"    32'h{m:08X}{',' if i else ''}" for i, m in reversed(list(enumerate(masks)))]
    out += ["  };", ""]
    by_class = {}
    for n, r in lane.items():
        by_class.setdefault(r["class"], []).append(f"8'h{r['addr']:02X}")
    out += ["  function automatic rclass_t lane_reg_class(input logic [7:0] a);", "    case (a)"]
    out += [f"      {', '.join(v)}: return {CLASS_CODE[c]};" for c, v in by_class.items()]
    out += ["      default: return RC_NONE;", "    endcase", "  endfunction", "",
            "  // bank word of a class-L lane register (0 for any other address)",
            "  function automatic logic [4:0] lane_reg_word(input logic [7:0] a);", "    case (a)"]
    out += [f"      LR_{n}: return LW_{n};" for n, r in lane.items() if "word" in r]
    out += ["      default: return 5'd0;", "    endcase", "  endfunction", "",
            "  // host (chip) map registers; arrays start at the address and step by STRIDE"]
    for n, r in chip.items():
        out.append(f"  localparam logic [15:0] HR_{n} = 16'h{r['addr']:04X};")
        if r["count"] > 1:
            out.append(f"  localparam int HR_{n}_COUNT = {r['count']}, HR_{n}_STRIDE = {r['stride']};")
        out.append(f"  localparam logic [31:0] HR_{n}_MASK = 32'h{r['mask']:08X};")
        emit_fields(out, f"HR_{n}", r)
    out += ["endpackage", "/* verilator lint_on UNUSEDPARAM */"]
    return "\n".join(out) + "\n"


def emit_py(tab, src_name):
    return (f"# generated by tools/gen_regs.py from {src_name}, do not edit\n"
            "# The checked register table; tools/tseasm/regs.py is the API on top of it.\n"
            f"TABLE = {pprint.pformat(tab, width=100, sort_dicts=False)}\n")


def main(argv):
    if len(argv) != 4:
        sys.exit(__doc__.strip().splitlines()[-1])
    try:
        tab = load(argv[1])
    except GenError as e:
        sys.exit(f"gen_regs: {argv[1]}: {e}")
    with open(argv[2], "w") as fh:
        fh.write(emit_sv(tab, argv[1]))
    with open(argv[3], "w") as fh:
        fh.write(emit_py(tab, argv[1]))
    print(f"gen_regs: {len(tab['lane'])} lane and {len(tab['chip'])} chip registers, "
          f"{tab['lane_bank_words']} bank words, no collisions")


if __name__ == "__main__":
    main(sys.argv)
