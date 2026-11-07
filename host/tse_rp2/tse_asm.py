# SPDX-License-Identifier: Apache-2.0
"""A lane assembler small enough for the board's REPL (MicroPython and CPython).

Same syntax and same words as the PC assembler, driven by the generated
table in tse_asm_table.py, without re, dataclasses or f-strings:
    label: MNEMONIC operands [delay] side n ; comment
Directives: .program .window .start .side_set .define .align .word .config.
Two passes: the first places labels, the second encodes. It checks fields
and register names but proves no timing; that is the PC compiler's job.
"""
from tse_rp2.tse_asm_table import ENUMS, INSTRS, LANE_CSR, SS, WAIT_IDX

MAX_WORDS = 256
PAD = INSTRS["DLY"]["value"]          # DLY 0: one-cycle no-op used by .align
PIN_DSTS = ("PINS", "PINDIRS")
IDENT_FIRST = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_"
IDENT_REST = IDENT_FIRST + "0123456789"


class AsmError(Exception):
    def __init__(self, msg, lineno=None):
        Exception.__init__(self, ("line %d: %s" % (lineno, msg)) if lineno else msg)
        self.lineno = lineno


class Program:
    def __init__(self):
        self.name = ""
        self.window = 0
        self.start = 0
        self.side_set = ""
        self.words = []
        self.labels = {}
        self.config = []


def is_ident(s):
    if not s or s[0] not in IDENT_FIRST:
        return False
    for c in s:
        if c not in IDENT_REST:
            return False
    return True


def _number(part):
    low = part.lower()
    try:
        if low.startswith("0x") and len(low) > 2:
            return int(low[2:], 16)
        if low.startswith("0b") and len(low) > 2:
            return int(low[2:], 2)
        if low.isdigit():
            return int(low, 10)
    except ValueError:
        pass
    return None


def value(tok, syms):
    """n, name, or a sum such as 4+i (decimal, 0x, 0b)."""
    text = tok.replace(" ", "")
    total, sign, seen, part = 0, 1, False, ""
    for c in text + "+":
        if c in "+-":
            if part:
                v = _number(part)
                if v is None:
                    if part not in syms:
                        raise AsmError("unknown symbol %s" % part)
                    v = syms[part]
                total, seen = total + sign * v, True
            sign = -1 if c == "-" else 1
            part = ""
        else:
            part += c
    if not seen:
        raise AsmError("missing value in '%s'" % tok)
    return total


def split_suffix(text):
    """Strip a trailing 'side n' and '[delay]', in either order."""
    side = delay = None
    while True:
        text = text.rstrip()
        if delay is None and text.endswith("]") and "[" in text:
            i = text.rfind("[")
            delay, text = text[i + 1:-1], text[:i]
            continue
        parts = text.rsplit(None, 2)
        if (side is None and len(parts) >= 2 and parts[-2].lower() == "side"
                and "[" not in parts[-1] and "]" not in parts[-1]):
            side = parts[-1]
            text = text[:len(text) - len(side)].rstrip()
            text = text[:len(text) - 4]
            continue
        return text, side, delay


def _fields(mn):
    return INSTRS[mn]["fields"]


def _field(mn, name):
    for n, f in INSTRS[mn]["fields"]:
        if n == name:
            return f
    return None


def lane_writable(addr):
    for name in LANE_CSR:
        if LANE_CSR[name][0] == addr:
            return LANE_CSR[name][1] in ("L", "F", "P")
    return False


def lane_readable(addr):
    for name in LANE_CSR:
        if LANE_CSR[name][0] == addr:
            return LANE_CSR[name][1] in ("F", "P", "R")
    return False


def lane_reg(addr):
    for name in LANE_CSR:
        if LANE_CSR[name][0] == addr:
            return name
    return None


def operand(mn, fname, tok, vals, syms):
    f = _field(mn, fname)
    up = tok.upper()
    if "enum" in f and up in ENUMS[f["enum"]]:
        return ENUMS[f["enum"]][up]
    if "enum_by" in f:
        names = ENUMS[_field(mn, f["enum_by"])["enum"]]
        group = None
        for k in names:
            if names[k] == vals[f["enum_by"]]:
                group = k
                break
        idx = WAIT_IDX.get(group, {})
        if up in idx:
            return idx[up]
    if f.get("kind") == "csr":
        a = LANE_CSR[up][0] if up in LANE_CSR else value(tok, syms)
        name = lane_reg(a) or "0x%02X" % a
        if mn == "CSRW" and not lane_writable(a):
            raise AsmError("CSRW %s: not a writable lane register" % name)
        if mn == "CSRR" and not lane_readable(a):
            raise AsmError("CSRR %s: programs cannot read it (configuration or unmapped)" % name)
        return a
    v = value(tok, syms)
    if "asm_max" in f and v > f["asm_max"]:
        raise AsmError("%s %s=%d: values above %d are reserved" % (mn, fname, v, f["asm_max"]))
    return v


def encode(mn, vals):
    """Instruction word from logical field values; missing fields 0, minus1 fields 1."""
    t = INSTRS[mn]
    ss = vals.get("ss", 0)
    if not 0 <= ss < (1 << (SS[0] - SS[1] + 1)):
        raise AsmError("%s: side-set %d outside 0 to %d" % (mn, ss, (1 << (SS[0] - SS[1] + 1)) - 1))
    word = t["value"] | ss << SS[1]
    raws = {}
    for n, f in t["fields"]:
        minus1 = f.get("kind") == "minus1"
        v = vals.get(n, 1 if minus1 else 0)
        top = (1 << (f["hi"] - f["lo"] + 1)) - 1
        lo, hi = (1, top + 1) if minus1 else (0, top)
        if not lo <= v <= hi:
            raise AsmError("%s: %s=%d outside %d to %d" % (mn, n, v, lo, hi))
        if "enum" in f and v not in ENUMS[f["enum"]].values():
            raise AsmError("%s: %s=%d is a reserved %s value" % (mn, n, v, f["enum"]))
        raws[n] = v - 1 if minus1 else v
        word |= raws[n] << f["lo"]
    for c in t["constraints"]:
        hit = True
        for k in c["if"]:
            if raws.get(k, 0) != c["if"][k]:
                hit = False
        if hit:
            for k in c["then"]:
                if raws.get(k, 0) != c["then"][k]:
                    raise AsmError("%s: %s must be %d when %s" % (
                        mn, k, c["then"][k], ", ".join(["%s=%d" % (a, c["if"][a]) for a in c["if"]])))
    return word


def encode_line(text, syms):
    """Encode one instruction (no label, no directive)."""
    text, side, delay = split_suffix(text.strip())
    sp = text.split(None, 1)
    mn = sp[0].upper() if sp else ""
    body = sp[1] if len(sp) > 1 else ""
    if mn not in INSTRS:
        raise AsmError("unknown instruction %s" % mn)
    fields = _fields(mn)
    flags, keys, tokens, positional = {}, {}, {}, []
    for n, f in fields:
        if "flag" in f:
            flags[f["flag"].lower()] = n
        if "key" in f:
            keys[f["key"].lower()] = n
        if "token" in f:
            tokens[f["token"].upper()] = n
        if not ("flag" in f or "key" in f or "set_by" in f or "token" in f) and n != "delay":
            positional.append(n)
    vals, pos, extra = {}, [], {}
    if side is not None:
        vals["ss"] = value(side, syms)
    if delay is not None:
        if _field(mn, "delay") is None:
            raise AsmError("%s has no [delay] field" % mn)
        vals["delay"] = value(delay, syms)
    ops = [o.strip() for o in body.split(",")] if body.strip() else []
    for op in ops:
        low = op.lower()
        if low in flags:
            vals[flags[low]] = 1
        elif low == "noblock" and "block" in flags:
            vals[flags["block"]] = 0
        elif "=" in op:
            k, v = op.split("=", 1)
            k, v = k.strip(), v.strip()
            if k.lower() in keys:
                n = keys[k.lower()]
                vals[n] = value(v, syms)
                for m, f in fields:
                    if f.get("set_by") == n:
                        vals[m] = 1
            else:
                extra[k.lower()] = v
        else:
            pos.append(op)
    if len(pos) != len(positional):
        raise AsmError("%s takes %d operand(s): %s" % (mn, len(positional), ", ".join(positional) or "none"))
    for i in range(len(pos)):
        n, op = positional[i], pos[i]
        if op.upper() in tokens:
            vals[tokens[op.upper()]], vals[n] = 1, 0
        else:
            vals[n] = operand(mn, n, op, vals, syms)
    if mn == "SET" and vals["dst"] in [ENUMS["setdst"][d] for d in PIN_DSTS]:
        mask = value(extra.pop("mask", "0b1111"), syms)
        if not (0 <= vals["imm"] <= 15 and 0 <= mask <= 15):
            raise AsmError("SET PINS/PINDIRS: value and mask are 4 bits")
        vals["imm"] |= mask << 4
    if extra:
        raise AsmError("%s: unknown key %s" % (mn, ", ".join(extra)))
    return encode(mn, vals)


def directive(prog, name, args, pc, syms, final):
    """Apply one directive; returns the number of words it adds at pc."""
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
        if not is_ident(k):
            raise AsmError("bad name %s" % k)
        if not final and k in syms:
            raise AsmError("%s defined twice" % k)
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
        if reg not in LANE_CSR or not lane_writable(LANE_CSR[reg][0]):
            raise AsmError(".config %s: not a writable lane register" % reg)
        if final:
            prog.config.append((reg, value(v, syms)))
    else:
        raise AsmError("unknown directive %s" % name)
    return 0


def run_pass(lines, syms, prog, final):
    pc = 0
    for lineno in range(1, len(lines) + 1):
        text = lines[lineno - 1].split(";", 1)[0].strip()
        try:
            i = text.find(":")
            if i > 0 and is_ident(text[:i]):
                label, text = text[:i], text[i + 1:].strip()
                if not final:
                    if label in syms:
                        raise AsmError("%s defined twice" % label)
                    syms[label] = pc
                    prog.labels[label] = pc
            if not text:
                continue
            if text.startswith("."):
                name, _, args = text.partition(" ")
                pc += directive(prog, name.lower(), args.strip(), pc, syms, final)
                continue
            if final:
                prog.words.append(encode_line(text, syms))
            pc += 1
            if pc > MAX_WORDS:
                raise AsmError("program longer than %d instructions" % MAX_WORDS)
        except AsmError as e:
            if e.lineno:
                raise
            raise AsmError(str(e), lineno)


def assemble(text):
    """Assemble program text into a Program; raises AsmError with the line number."""
    lines = text.replace("\r", "").split("\n")
    prog = Program()
    syms = {}
    run_pass(lines, syms, prog, False)
    defines = {}
    for k in syms:
        if k not in prog.labels:
            defines[k] = syms[k]
    syms = dict(prog.labels)
    syms.update(defines)
    run_pass(lines, syms, prog, True)
    return prog


def sram_words(insns):
    """Two instructions per 32-bit SRAM word, the even address in bits 15:0."""
    ws = list(insns) + ([PAD] if len(insns) % 2 else [])
    return [ws[i] | (ws[i + 1] << 16) for i in range(0, len(ws), 2)]
