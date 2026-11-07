# SPDX-License-Identifier: Apache-2.0
"""Assembler for TSE lane programs.

One instruction per line, pioasm-like:
    label: MNEMONIC operands [delay] side n ; comment
The operand syntax is read from the ISA table: an instruction's fields in
table order are its positional operands, except flag fields (keywords such as
block, to, alt, cclr), key fields (gsel=g), token fields (STW X) and the field
named delay, written [n]. Only SET is special: SET PINS and SET PINDIRS take
a value and mask= (default 0b1111), packed as imm = mask << 4 | value.

Directives: .program name, .window w, .start pc, .side_set n [opt],
.define name value, .align n, .word value, .config REG value.
Two passes: the first places labels, the second encodes.
"""
import re
from dataclasses import dataclass, field

from . import isa, regs

MAX_WORDS = 256                   # 8-bit PC: a 128-word window holds 256 instructions
PAD = isa.encode("DLY", d=0)      # .align filler: a one-cycle no-op
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
PIN_DSTS = ("PINS", "PINDIRS")


class AsmError(Exception):
    def __init__(self, msg, lineno=None):
        super().__init__(f"line {lineno}: {msg}" if lineno else msg)
        self.lineno = lineno


@dataclass
class Program:
    name: str = ""
    window: int = 0
    start: int = 0
    side_set: str = ""
    words: list = field(default_factory=list)
    labels: dict = field(default_factory=dict)
    config: list = field(default_factory=list)    # (register, value)
    source: list = field(default_factory=list)    # (pc, line text) for the listing


def value(tok, syms):
    """Evaluate n, name, or a sum such as 4+i (decimal, 0x, 0b)."""
    total, sign, seen = 0, 1, False
    for part in re.split(r"([+-])", tok.replace(" ", "")):
        if part in ("+", "-"):
            sign = -1 if part == "-" else 1
            continue
        if not part:
            continue
        low = part.lower()
        if re.fullmatch(r"0x[0-9a-f]+|0b[01]+", low):
            v = int(low, 0)
        elif part.isdigit():
            v = int(part, 10)
        elif part in syms:
            v = syms[part]
        else:
            raise AsmError(f"unknown symbol {part}")
        total, sign, seen = total + sign * v, 1, True
    if not seen:
        raise AsmError(f"missing value in '{tok}'")
    return total


def split_suffix(text):
    """Strip a trailing 'side n' and '[delay]', in either order."""
    side = delay = None
    while True:
        m = re.search(r"\bside\s+([^\s\[\]]+)\s*$", text, re.I)
        if m and side is None:
            side, text = m.group(1), text[:m.start()].rstrip()
            continue
        m = re.search(r"\[([^\]]*)\]\s*$", text)
        if m and delay is None:
            delay, text = m.group(1), text[:m.start()].rstrip()
            continue
        return text, side, delay


def operand(mn, fname, tok, vals, syms):
    f = isa.INSTRS[mn]["fields"][fname]
    up = tok.upper()
    if "enum" in f and up in isa.ENUMS[f["enum"]]:
        return isa.ENUMS[f["enum"]][up]
    if "enum_by" in f:
        by = isa.INSTRS[mn]["fields"][f["enum_by"]]["enum"]
        group = {v: k for k, v in reversed(isa.ENUMS[by].items())}[vals[f["enum_by"]]]
        names = isa.WAIT_IDX.get(group, {})
        if up in names:
            return names[up]
    if f.get("kind") == "csr":
        a = regs.LANE[up]["addr"] if up in regs.LANE else value(tok, syms)
        name = regs.lane_reg(a) or f"0x{a:02X}"
        if mn == "CSRW" and not regs.lane_writable(a):
            raise AsmError(f"CSRW {name}: not a writable lane register")
        if mn == "CSRR" and not regs.lane_readable(a):
            raise AsmError(f"CSRR {name}: programs cannot read it (configuration or unmapped)")
        return a
    v = value(tok, syms)
    if "asm_max" in f and v > f["asm_max"]:
        raise AsmError(f"{mn} {fname}={v}: values above {f['asm_max']} are reserved")
    return v


def encode_line(text, syms):
    """Encode one instruction (no label, no directive) with the given symbols."""
    text, side, delay = split_suffix(text.strip())
    mn, body = (re.split(r"\s+", text, maxsplit=1) + [""])[:2]
    mn = mn.upper()
    if mn not in isa.INSTRS:
        raise AsmError(f"unknown instruction {mn}")
    fields = isa.INSTRS[mn]["fields"]
    flags = {f["flag"].lower(): n for n, f in fields.items() if "flag" in f}
    keys = {f["key"].lower(): n for n, f in fields.items() if "key" in f}
    tokens = {f["token"].upper(): n for n, f in fields.items() if "token" in f}
    positional = [n for n, f in fields.items()
                  if not {"flag", "key", "set_by", "token"} & set(f) and n != "delay"]
    vals, pos, extra = {}, [], {}
    if side is not None:
        vals["ss"] = value(side, syms)
    if delay is not None:
        if "delay" not in fields:
            raise AsmError(f"{mn} has no [delay] field")
        vals["delay"] = value(delay, syms)
    for op in [o.strip() for o in body.split(",")] if body.strip() else []:
        low = op.lower()
        if low in flags:
            vals[flags[low]] = 1
        elif low == "noblock" and "block" in flags:
            vals[flags["block"]] = 0
        elif "=" in op:
            k, v = (s.strip() for s in op.split("=", 1))
            if k.lower() in keys:
                n = keys[k.lower()]
                vals[n] = value(v, syms)
                vals.update({m: 1 for m, f in fields.items() if f.get("set_by") == n})
            else:
                extra[k.lower()] = v
        else:
            pos.append(op)
    if len(pos) != len(positional):
        raise AsmError(f"{mn} takes {len(positional)} operand(s): {', '.join(positional) or 'none'}")
    for n, op in zip(positional, pos):
        if op.upper() in tokens:
            vals[tokens[op.upper()]], vals[n] = 1, 0
        else:
            vals[n] = operand(mn, n, op, vals, syms)
    if mn == "SET" and vals["dst"] in (isa.ENUMS["setdst"][d] for d in PIN_DSTS):
        mask = value(extra.pop("mask", "0b1111"), syms)
        if not (0 <= vals["imm"] <= 15 and 0 <= mask <= 15):
            raise AsmError("SET PINS/PINDIRS: value and mask are 4 bits")
        vals["imm"] |= mask << 4
    if extra:
        raise AsmError(f"{mn}: unknown key {', '.join(extra)}")
    try:
        return isa.encode(mn, **vals)
    except isa.IsaError as e:
        raise AsmError(str(e)) from None


def strip_comment(line):
    return line.split(";", 1)[0].strip()


def directive(prog, name, args, pc, syms, final):
    """Apply one directive. Returns the number of words it adds at pc."""
    if name == ".program":
        prog.name = args
    elif name == ".window":
        prog.window = value(args, syms)
        if not 0 <= prog.window <= 3:
            raise AsmError(".window must be 0 to 3")
    elif name == ".start":
        if final:
            prog.start = value(args, syms)
            if not 0 <= prog.start < MAX_WORDS:
                raise AsmError(".start out of range")
    elif name == ".side_set":
        prog.side_set = args
    elif name == ".define":
        k, _, v = args.partition(" ")
        if not IDENT.match(k):
            raise AsmError(f"bad name {k}")
        if not final and k in syms:
            raise AsmError(f"{k} defined twice")
        syms[k] = value(v, syms)
    elif name == ".align":
        n = value(args, syms)
        if n < 1 or n & (n - 1):
            raise AsmError(".align takes a power of two")
        pad = -pc % n
        if final:
            prog.words += [PAD] * pad
        return pad
    elif name == ".word":
        if final:
            v = value(args, syms)
            if not 0 <= v <= 0xFFFF:
                raise AsmError(".word takes 16 bits")
            prog.words.append(v)
        return 1
    elif name == ".config":
        reg, _, v = args.partition(" ")
        reg = reg.upper()
        if reg not in regs.LANE or not regs.lane_writable(regs.LANE[reg]["addr"]):
            raise AsmError(f".config {reg}: not a writable lane register")
        if final:
            val = value(v, syms)
            try:
                regs.check(reg, val)
            except regs.RegError as e:
                raise AsmError(str(e)) from None
            prog.config.append((reg, val))
    else:
        raise AsmError(f"unknown directive {name}")
    return 0


def run_pass(lines, syms, prog, final):
    pc = 0
    for lineno, raw in enumerate(lines, 1):
        text = strip_comment(raw)
        try:
            m = re.match(r"([A-Za-z_][A-Za-z0-9_]*):", text)
            if m:
                label, text = m.group(1), text[m.end():].strip()
                if not final:
                    if label in syms:
                        raise AsmError(f"{label} defined twice")
                    syms[label] = pc
                    prog.labels[label] = pc
            if not text:
                continue
            if text.startswith("."):
                name, _, args = text.partition(" ")
                n = directive(prog, name.lower(), args.strip(), pc, syms, final)
                if final and name.lower() == ".word":
                    prog.source.append((pc, raw.rstrip()))
                pc += n
                continue
            if final:
                prog.words.append(encode_line(text, syms))
                prog.source.append((pc, raw.rstrip()))
            pc += 1
            if pc > MAX_WORDS:
                raise AsmError(f"program longer than {MAX_WORDS} instructions")
        except AsmError as e:
            raise AsmError(str(e), lineno) from None


def assemble(text):
    """Assemble program text into a Program; raises AsmError with the line number."""
    lines = text.splitlines()
    prog = Program()
    syms = {}
    run_pass(lines, syms, prog, final=False)
    defines = {k: v for k, v in syms.items() if k not in prog.labels}
    syms = dict(prog.labels)
    syms.update(defines)
    run_pass(lines, syms, prog, final=True)
    return prog


def hex16(words):
    return "".join(f"{w:04X}\n" for w in words)


def hex32(words):
    """SRAM words: two instructions each, the even address in bits 15:0."""
    ws = list(words) + ([PAD] if len(words) % 2 else [])
    return "".join(f"{ws[i + 1]:04X}{ws[i]:04X}\n" for i in range(0, len(ws), 2))


def listing(prog):
    return "".join(f"{pc:3d}  {prog.words[pc]:04X}  {src.strip()}\n" for pc, src in prog.source)
