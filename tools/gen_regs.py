#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate the SystemVerilog register package and the Python register table from isa/tse_regs.yaml.

The table is checked before anything is written. It stops with a message on
two registers at one address (arrays expanded), a register outside the lane
window or outside every host region, overlapping regions, a field that leaves
the 32-bit word or overlaps another, a limit or enum that does not fit its
field, a require rule on an unknown field, a class-L chip register without a
known bank (or a bank on another class), and any configuration bank with more
words than bank_words.

Class-L registers get bank words 0, 1, 2, ... in address order within their
bank (arrays take consecutive words): the lane bank for lane registers, the
named chip bank for chip registers. Banks are addressed by word (5 bits);
lane_reg_word() and chip_reg_cfg() map register addresses to bank words.

usage: gen_regs.py isa/tse_regs.yaml src/tse_regs_pkg.sv tools/tseasm/regs_table.py
"""
import pprint
import sys

import yaml

REG_KEYS = {"addr", "class", "fields", "doc", "count", "stride", "host", "require", "bank"}
FIELD_KEYS = {"bits", "min", "max", "enum"}
WORD_BITS = 5
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
    out = {
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
    if "bank" in r:
        out["bank"] = r["bank"]
    return out


def assign_words(where, regs, bank_words):
    """Give class-L registers consecutive bank words in address order; return the word count."""
    word = 0
    for name in sorted(regs, key=lambda n: regs[n]["addr"]):
        r = regs[name]
        if r["count"] > 1 and r["stride"] != 1:
            raise GenError(f"{name}: a class-L array needs stride 1")
        r["word"] = word
        word += r["count"]
    if word > bank_words:
        raise GenError(f"bank {where}: {word} words exceed the {bank_words}-word bank")
    return word


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
    bank_words = src["bank_words"]
    if not 1 <= bank_words <= 1 << WORD_BITS:
        raise GenError(f"bank_words {bank_words} does not fit a {WORD_BITS}-bit word address")
    for name, r in lane.items():
        if "bank" in r:
            raise GenError(f"{name}: lane registers always use the lane bank")
    banks = list(src["chip_banks"])
    for name, r in chip.items():
        if r["class"] == "L" and r.get("bank") not in banks:
            raise GenError(f"{name}: class-L chip register needs a bank from chip_banks")
        if r["class"] != "L" and "bank" in r:
            raise GenError(f"{name}: only class-L registers live in a bank")
    used = {"lane": assign_words("lane", {n: r for n, r in lane.items() if r["class"] == "L"}, bank_words)}
    for b in banks:
        used[b] = assign_words(b, {n: r for n, r in chip.items() if r.get("bank") == b}, bank_words)
    return {
        "version": src["version"],
        "classes": {k: dict(v) for k, v in classes.items()},
        "lane_window": dict(win),
        "regions": {k: dict(v) for k, v in src["regions"].items()},
        "bank_words": bank_words,
        "chip_banks": banks,
        "bank_used": used,
        "lane_bank_words": used["lane"],
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
           "  typedef logic [2:0] rclass_t;",
           "  localparam rclass_t RC_NONE = 3'd0, RC_L = 3'd1, RC_F = 3'd2, RC_P = 3'd3, RC_R = 3'd4;",
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
    bw = tab["bank_words"]
    out += ["", f"  localparam int BANK_WORDS = {bw};",
            f"  localparam int LANE_BANK_WORDS = {tab['lane_bank_words']};",
            f"  // bits that exist in each bank word, word {bw - 1} first (the bank's MASK parameter)"]
    emit_mask(out, "LANE_BANK_MASK", bank_masks(lane, None, bw))
    banks = tab["chip_banks"]
    cbw = max(1, (len(banks) - 1).bit_length())
    out += ["  // chip configuration banks (one shared write port, bank index and 5-bit word)",
            f"  localparam int CHIP_BANKS = {len(banks)};"]
    for i, b in enumerate(banks):
        out.append(f"  localparam logic [{cbw - 1}:0] CBANK_{b.upper()} = {cbw}'d{i};")
        out.append(f"  localparam int CHIP_{b.upper()}_WORDS = {tab['bank_used'][b]};")
        emit_mask(out, f"CHIP_{b.upper()}_MASK", bank_masks(chip, b, bw))
    out.append("")
    by_class = {}
    for n, r in lane.items():
        by_class.setdefault(r["class"], []).append(f"8'h{r['addr']:02X}")
    out += ["  function automatic logic [2:0] lane_reg_class(input logic [7:0] a);", "    case (a)"]
    out += [f"      {', '.join(v)}: lane_reg_class = {CLASS_CODE[c]};" for c, v in by_class.items()]
    out += ["      default: lane_reg_class = RC_NONE;", "    endcase", "  endfunction", "",
            "  // bank word of a class-L lane register (0 for any other address)",
            "  function automatic logic [4:0] lane_reg_word(input logic [7:0] a);", "    case (a)"]
    out += [f"      LR_{n}: lane_reg_word = LW_{n};" for n, r in lane.items() if "word" in r]
    out += ["      default: lane_reg_word = 5'd0;", "    endcase", "  endfunction", "",
            "  // host (chip) map registers; arrays start at the address and step by STRIDE"]
    for n, r in chip.items():
        out.append(f"  localparam logic [15:0] HR_{n} = 16'h{r['addr']:04X};")
        if r["count"] > 1:
            out.append(f"  localparam int HR_{n}_COUNT = {r['count']}, HR_{n}_STRIDE = {r['stride']};")
        out.append(f"  localparam logic [31:0] HR_{n}_MASK = 32'h{r['mask']:08X};")
        if "word" in r:
            out.append(f"  localparam logic [{cbw - 1}:0] CB_{n} = CBANK_{r['bank'].upper()};")
            out.append(f"  localparam logic [4:0] CW_{n} = 5'd{r['word']};")
        emit_fields(out, f"HR_{n}", r)
    out += ["",
            "  typedef struct packed {",
            "    logic hit;",
            f"    logic [{cbw - 1}:0] bank;",
            "    logic [4:0] word;",
            "  } chip_cfg_t;", "",
            "  // bank and word of a class-L host address (hit 0 for any other address)",
            "  function automatic chip_cfg_t chip_reg_cfg(input logic [15:0] a);",
            "    chip_cfg_t r;",
            "    r = '0;"]
    for n, r in chip.items():
        if "word" not in r:
            continue
        if r["count"] > 1:
            out += [f"    if (a >= HR_{n} && a <= HR_{n} + 16'd{r['count'] - 1}) begin",
                    f"      r.hit = 1'b1; r.bank = CB_{n}; r.word = CW_{n} + 5'(a - HR_{n});"]
        else:
            out += [f"    if (a == HR_{n}) begin",
                    f"      r.hit = 1'b1; r.bank = CB_{n}; r.word = CW_{n};"]
        out.append("    end")
    out += ["    chip_reg_cfg = r;", "  endfunction", "endpackage", "/* verilator lint_on UNUSEDPARAM */"]
    return "\n".join(out) + "\n"


def bank_masks(regs, bank, bank_words):
    masks = [0] * bank_words
    for r in regs.values():
        if "word" in r and r.get("bank") == bank:
            for i in range(r["count"]):
                masks[r["word"] + i] = r["mask"]
    return masks


def emit_mask(out, name, masks):
    out.append(f"  localparam logic [{32 * len(masks) - 1}:0] {name} = {{")
    out += [f"    32'h{m:08X}{',' if i else ''}" for i, m in reversed(list(enumerate(masks)))]
    out.append("  };")


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
          "bank words " + ", ".join(f"{b} {n}" for b, n in tab["bank_used"].items())
          + f" (of {tab['bank_words']}), no collisions")


if __name__ == "__main__":
    main(sys.argv)
