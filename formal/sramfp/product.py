#!/usr/bin/env python3
"""Build the shared-state product of the flattened chip.

Input: the RTLIL of the flattened chip after clk2fflogic: every flop, latch
and asynchronous reset is a global-clock $ff plus logic, so a transparent
latch or a reset stays logic in each copy. Output: module fp_core with two
copies of that logic, a and b. They share the top-level inputs and every
storage element: copy b has no $ff of its own, each of its $ff outputs is
wired to the same $ff of copy a. Each copy keeps its own two SRAM
stand-ins (u_macro_lo and u_macro_hi), so the read data of the two copies
are independent free values. The kept SRAM input wires (fp_*) of each macro
in both copies become outputs a_lo_*, a_hi_*, b_lo_* and b_hi_*.
With the state shared, a difference between a_* and b_* in a cycle can only
come from the read data of that cycle, through combinational logic.

usage: product.py chip.il fp_core.il
"""
import re
import sys

FF_TYPES = {"$ff"}
OTHER_STORAGE = {"$dff", "$dffe", "$adff", "$adffe", "$aldff", "$aldffe", "$dffsr", "$dffsre",
                 "$sdff", "$sdffe", "$sdffce", "$dlatch", "$adlatch", "$dlatchsr", "$sr",
                 "$mem", "$mem_v2"}
KEEP = ("fp_men", "fp_wen", "fp_ren", "fp_addr", "fp_din")
MACROS = ("u_macro_lo", "u_macro_hi")


def ren(tok, c):
    return tok[0] + c + "." + tok[1:]


def sig(text, c):
    return " ".join(ren(t, c) if t[0] in "\\$" else t for t in text.split())


def parse(lines):
    """Split the single module into items: ('wire', attrs, line),
    ('cell', attrs, lines), ('connect', line)."""
    items, attrs, i = [], [], 0
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith("attribute"):
            attrs.append(lines[i])
        elif s.startswith("wire "):
            items.append(("wire", attrs, lines[i]))
            attrs = []
        elif s.startswith("cell "):
            body = [lines[i]]
            while lines[i].strip() != "end":
                i += 1
                body.append(lines[i])
            items.append(("cell", attrs, body))
            attrs = []
        elif s.startswith("connect "):
            items.append(("connect", None, lines[i]))
        elif s.startswith(("memory", "process", "switch")):
            sys.exit(f"product.py: unexpected RTLIL item: {s}")
        i += 1
    return items


def copy_of(items, c, ports, out):
    shared = c == "b"
    for kind, attrs, x in items:
        if kind == "wire":
            toks = x.split()
            name = toks[-1]
            keep = [t for t in toks[1:-1]]
            opts, j = [], 0
            while j < len(keep):
                if keep[j] in ("input", "output", "inout"):
                    j += 2
                    continue
                opts.append(keep[j])
                j += 1
            for a in attrs:
                if not (shared and a.split()[1] == "\\init"):
                    out.append(a)
            out.append("  wire " + " ".join(opts + [ren(name, c)]))
            if "input" in toks:
                out.append(f"  connect {ren(name, c)} {name}")
                ports.setdefault(name, x)
        elif kind == "cell":
            head = x[0].split()
            ctype, cname = head[1], head[2]
            if ctype == "$scopeinfo":
                continue
            if ctype in OTHER_STORAGE:
                sys.exit(f"product.py: storage cell {ctype} left, run clk2fflogic first")
            if shared and ctype in FF_TYPES:
                q = [ln.split(None, 2)[2] for ln in x if ln.split()[:2] == ["connect", "\\Q"]]
                out.append(f"  connect {sig(q[0], 'b')} {sig(q[0], 'a')}")
                continue
            out.extend(attrs)
            out.append(f"  cell {ctype} {ren(cname, c)}")
            for ln in x[1:-1]:
                t = ln.split(None, 2)
                if t[0] == "connect":
                    out.append(f"    connect {t[1]} {sig(t[2], c)}")
                else:
                    out.append(ln)
            out.append("  end")
        else:
            lhs_rhs = x.split(None, 1)[1]
            out.append("  connect " + sig(lhs_rhs, c))


def main():
    src, dst = sys.argv[1], sys.argv[2]
    lines = open(src).read().splitlines()
    mods = [i for i, ln in enumerate(lines) if ln.startswith("module ")]
    if len(mods) != 1:
        sys.exit("product.py: expected one flattened module")
    end = max(i for i, ln in enumerate(lines) if ln == "end")
    items = parse(lines[mods[0] + 1:end])
    wires = {x.split()[-1]: x for k, _, x in items if k == "wire"}
    found = {}
    for m in MACROS:
        for k in KEEP:
            hits = [n for n in wires if n.endswith(f".{m}.{k}")]
            if len(hits) != 1:
                sys.exit(f"product.py: SRAM stand-in wire {m}.{k} not found once: {hits}")
            found[m.split("_")[-1] + "_" + k[3:]] = hits[0]
    print("product.py: bound " + " ".join(found.values()))

    body, ports = [], {}
    copy_of(items, "a", ports, body)
    copy_of(items, "b", ports, body)

    head, n = [], 1
    for name, decl in ports.items():
        w = re.search(r"width (\d+)", decl)
        head.append(f"  wire {'width ' + w.group(1) + ' ' if w else ''}input {n} {name}")
        n += 1
    for c in "ab":
        for k, w in found.items():
            m = re.search(r"width (\d+)", wires[w])
            p = f"\\{c}_{k}"
            head.append(f"  wire {'width ' + m.group(1) + ' ' if m else ''}output {n} {p}")
            body.append(f"  connect {p} {ren(w, c)}")
            n += 1

    with open(dst, "w") as f:
        if lines[0].startswith("autoidx"):
            f.write(lines[0] + "\n")
        f.write("module \\fp_core\n")
        f.write("\n".join(head + body) + "\n")
        f.write("end\n")


if __name__ == "__main__":
    main()
