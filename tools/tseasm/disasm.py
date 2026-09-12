"""Disassembler for TSE lane programs, the inverse of asm.py, driven by the same table.

Every word comes back as text that reassembles to the same word. A word the
syntax cannot spell (illegal, or legal but refused by the assembler: CSRW to a
read-only register, CSRR of a configuration word, TMR gsel without gwe, an
EMIT code reserved for the event unit) comes back as `.word 0x....`, followed
by its decoded form as a comment when it decodes.
"""
from . import isa, regs
from .asm import AsmError, PIN_DSTS, encode_line


def name_of(names, v):
    """First (canonical) name with value v, or None."""
    return next((k for k, x in names.items() if x == v), None)


def operand_text(name, fname, f, vals, labels):
    v = vals[fname]
    if "enum" in f:
        return name_of(isa.ENUMS[f["enum"]], v)
    if "enum_by" in f:
        by = isa.INSTRS[name]["fields"][f["enum_by"]]["enum"]
        group = name_of(isa.ENUMS[by], vals[f["enum_by"]])
        return name_of(isa.WAIT_IDX.get(group, {}), v) or str(v)
    if f.get("kind") == "addr":
        return labels.get(v, str(v))
    if f.get("kind") == "csr":
        return regs.lane_reg(v) or f"0x{v:02X}"
    return str(v)


def format_instr(ins, labels=None):
    """Assembler text for a decoded instruction, or None if the syntax cannot spell it."""
    labels = labels or {}
    fields = isa.INSTRS[ins.name]["fields"]
    vals = ins.fields
    token = next((f["token"] for n, f in fields.items() if "token" in f and vals[n]), None)
    pos, tail = [], []
    for n, f in fields.items():
        if "flag" in f:
            if vals[n]:
                tail.append(f["flag"])
        elif "key" in f:
            setter = next(m for m, g in fields.items() if g.get("set_by") == n)
            if vals[setter]:
                tail.append(f"{f['key']}={vals[n]}")
            elif vals[n]:
                return None
        elif "set_by" in f or "token" in f or n == "delay":
            continue
        elif token:
            pos.append(token)
        else:
            pos.append(operand_text(ins.name, n, f, vals, labels))
    if ins.name == "SET" and pos[0] in PIN_DSTS:
        imm = vals["imm"]
        pos[1] = f"0b{imm & 15:04b}, mask=0b{imm >> 4:04b}"
    text = ins.name + (" " + ", ".join(pos + tail) if pos or tail else "")
    if vals.get("delay"):
        text += f" [{vals['delay']}]"
    if vals["ss"]:
        text += f" side {vals['ss']}"
    return text


def disasm_word(word, labels=None):
    """Text for one word; labels maps target address to label name."""
    labels = labels or {}
    ins = isa.decode(word)
    text = format_instr(ins, labels) if ins else None
    if text is not None:
        try:
            if encode_line(text, {n: a for a, n in labels.items()}) == word:
                return text
        except AsmError:
            pass
    return f".word 0x{word:04X}" + (f" ; {text}" if text else "")


def disasm(words):
    """Program text for a list of words, with labels L<n> on jump targets inside it."""
    targets = set()
    for w in words:
        ins = isa.decode(w)
        if ins and ins.name == "JMP" and ins.fields["target"] < len(words):
            targets.add(ins.fields["target"])
    labels = {t: f"L{t}" for t in sorted(targets)}
    out = []
    for pc, w in enumerate(words):
        if pc in labels:
            out.append(f"{labels[pc]}:")
        out.append("        " + disasm_word(w, labels))
    return "\n".join(out) + "\n"
