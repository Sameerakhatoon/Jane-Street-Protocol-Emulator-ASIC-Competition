"""Stage 6, code generation: scheduled instructions to words, listing and loader image.

Every word is decoded again and disassembled; a mismatch with what was
encoded is an internal error (exit 3), never a silently wrong image.
The image is $readmemh text: lane k's program starts at instruction k * 256,
its SRAM window.
"""
from . import isa
from .diag import TseError


def resolved(ins, labels):
    f = dict(ins.a)
    if ins.op == "JMP":
        f["target"] = labels[f["target"]]
        if f["target"] >= isa.MAX_INSTRUCTIONS:
            raise TseError("R301", "program too large: jump target beyond 256 instructions")
    f.pop("expect_taken", None)
    return f


class InternalError(Exception):
    pass


def assemble(att):
    """[(address, word, label, text, note)] for one scheduled role."""
    names = {i: name for name, i in att.labels.items()}
    rows = []
    for i, ins in enumerate(att.low.prog):
        f = resolved(ins, att.labels)
        w = isa.encode(ins.op, f)
        back = isa.decode(w)
        if back is None or isa.fmt(*back, names) != isa.fmt(ins.op, f, names):
            raise InternalError(f"decoder mismatch at {i}: {back} vs {ins.op} {f}")
        note = ins.note or (ins.ob.name if ins.ob else "")
        rows.append((i, w, names.get(i), isa.fmt(*back, names), note))
    return rows


def listing_lines(rows):
    out = ["addr  word   instruction                    note"]
    for i, w, label, text, note in rows:
        lab = f"{label}:" if label else ""
        out.append(f"{i:3d}  {w:04x}   {lab:<7}{text:<30} {note}")
    return out


def image_text(spec_name, roles):
    """roles: [(lane, role name, rows)]."""
    out = [f"// {spec_name}: TSE loader image, one 16-bit instruction per line ($readmemh)"]
    for lane, name, rows in roles:
        out.append(f"// lane {lane}: role {name}, {len(rows)} instructions")
        out.append(f"@{lane * isa.MAX_INSTRUCTIONS:03x}")
        out += [f"{w:04x}" for _, w, _, _, _ in rows]
    return "\n".join(out) + "\n"
