"""Driver: `python3 -m tools.tsec COMMAND SPEC [options]`.

  lex SPEC        tokens, one per line
  parse SPEC      the syntax tree
  fmt SPEC        the spec, pretty printed
  check SPEC      front end only: parse, resolve, semantic checks
  build SPEC      the whole pipeline; with -o DIR writes NAME.img, NAME.lst,
                  NAME.report.txt (and with --emit all also UPPAAL XML and the
                  proof harness), only if every obligation is proven (rule R1)
  selftest        ISA tables and cycle model against the published ISA numbers

Exit codes: 0 ok, 1 spec errors, 2 rejected, 3 internal error.
"""
import argparse
import os
import sys
import traceback
from fractions import Fraction as Fr

from . import ast, printer, proofgen, report, selftest, uppaal
from .check import check
from .codegen import assemble, image_text, listing_lines
from .diag import EXIT_ERRORS, EXIT_INTERNAL, EXIT_OK, EXIT_REJECTED, Errors, TseError, render
from .lexer import lex
from .parser import parse
from .resolve import resolve
from .schedule import compile_role
from .timing import FetchModel

LANES = 3                       # lanes in the 6x4 build
TITLES = {"R310": "output placement outside tolerance", "R311": "response window not met",
          "R312": "sample placement outside tolerance", "R313": "hold too short",
          "R314": "re-arm too late", "R317": "bit-timer pacing overrun"}


def arg_parser():
    ap = argparse.ArgumentParser(prog="tsec", description="TSE timed-spec compiler")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("lex", "parse", "fmt", "check", "build"):
        p = sub.add_parser(name)
        p.add_argument("spec")
        if name in ("check", "build"):
            p.add_argument("-D", action="append", default=[], metavar="NAME=VALUE",
                           help="override a param (SI units)")
        if name == "build":
            p.add_argument("-o", dest="out", help="output directory")
            p.add_argument("--fclk", type=float, default=60e6)
            p.add_argument("--slots", type=int, default=4, help="fetch slot period S")
            p.add_argument("--entries", type=int, default=2, help="fetch buffer words per lane")
            p.add_argument("--lat", type=int, default=1, help="fetch_latency (2: registered A_DOUT)")
            p.add_argument("--ppm", type=int, default=50, help="clock budget against the peer")
            p.add_argument("--emit", default="img,lst,report",
                           help="comma list of img, lst, report, xml, proof, or all")
    sub.add_parser("selftest")
    return ap


def show(errs, src, path):
    for e in errs:
        print(render(e, src, path), file=sys.stderr)


def front_end(src, path, overrides=None):
    """parse, resolve, check; returns the symbol table or prints errors and returns None."""
    try:
        sym = resolve(parse(src), overrides)
        check(sym)
        return sym
    except Errors as e:
        show(e.errs, src, path)
        print(f"{len(e.errs)} error(s); nothing compiled", file=sys.stderr)
        return None


def overrides_of(defs):
    out = {}
    for d in defs:
        name, sep, value = d.partition("=")
        if not sep:
            raise SystemExit(f"tsec: -D needs NAME=VALUE, got '{d}'")
        out[name] = value
    return out


def rejection_diags(res):
    """One diagnostic per unproven obligation of the attempt that came closest."""
    best = min(reversed(res.tried), key=lambda t: len({(v.code, v.span) for v in t.report.violations}))
    others = [t for t in res.tried if t is not best]
    seen, out = set(), []
    for v in best.report.violations:
        if (v.code, v.span) in seen:
            continue
        seen.add((v.code, v.span))
        detail = v.detail or (v.text.split(": ", 1)[1] if v.code in ("R313", "R314") else v.text)
        notes = []
        for t in others:
            same = next((o for o in t.report.violations if o.code == v.code and o.span == v.span), None)
            name = report.STRATEGY_NAME[t.mode]
            notes.append(f"with {name}: {same.detail or same.text}" if same else
                         f"with {name}: this obligation holds, but others fail")
        notes.append(f"hint: {best.hint}")
        title = TITLES.get(v.code, "obligation unproven")
        out.append(TseError(v.code, f"{title} in role '{res.role.name.text}': {detail}", v.span, notes))
    return sorted(out, key=lambda e: (e.span.line, e.span.col))


def build(args, src, path):
    sym = front_end(src, path, overrides_of(args.D))
    if sym is None:
        return EXIT_ERRORS
    fclk = Fr(int(args.fclk))
    fm = FetchModel(args.slots, args.entries, args.lat)
    emit = {"img", "lst", "report", "xml", "proof"} if args.emit == "all" else set(args.emit.split(","))
    text = report.header(sym, fclk, fm, args.ppm)
    rejected, accepted = [], []
    for lane, role in enumerate(sym.roles):
        if lane >= LANES:
            rejected.append(TseError("R306", f"more roles than lanes: role '{role.name.text}' "
                                     f"would need lane {lane}, the chip has {LANES}", role.name.span))
            continue
        try:
            res = compile_role(sym, role, lane, fclk, fm, args.ppm)
            rows = assemble(res.chosen) if res.chosen else None
        except TseError as e:
            text += ["", f"== role {role.name.text} ({role.kind}) on lane {lane}",
                     f"REJECTED: {e.msg} ({e.code})"]
            rejected.append(e)
            continue
        text += report.role_section(res, rows)
        if rows is None:
            rejected += rejection_diags(res)
        else:
            accepted.append((lane, res, rows))
    text += report.verdict(not rejected)
    print("\n".join(text))
    stem = os.path.splitext(os.path.basename(path))[0]
    if rejected:
        show(rejected, src, path)
        print(f"{len(rejected)} obligation(s) or limit(s) not met; no image written (rule R1)",
              file=sys.stderr)
        if args.out:
            stale = os.path.join(args.out, f"{stem}.img")
            if os.path.exists(stale):
                os.remove(stale)
        return EXIT_REJECTED
    if args.out:
        write_outputs(args.out, stem, sym, accepted, text, emit)
    return EXIT_OK


def write_outputs(out, stem, sym, accepted, text, emit):
    os.makedirs(out, exist_ok=True)

    def put(name, body):
        with open(os.path.join(out, name), "w") as fh:
            fh.write(body)
    if "img" in emit:
        put(f"{stem}.img", image_text(sym.spec.name.text,
                                      [(lane, res.role.name.text, rows) for lane, res, rows in accepted]))
    if "lst" in emit:
        lst = []
        for lane, res, rows in accepted:
            lst += [f"; lane {lane}: role {res.role.name.text}, "
                    f"{report.STRATEGY_NAME[res.chosen.mode]}"] + listing_lines(rows) + [""]
        put(f"{stem}.lst", "\n".join(lst))
    if "report" in emit:
        put(f"{stem}.report.txt", "\n".join(text) + "\n")
    if "xml" in emit:
        for _, res, _ in accepted:
            put(f"{stem}_{res.role.name.text}.xml", uppaal.export(res.role, res.chosen.low))
    if "proof" in emit:
        for lane, res, rows in accepted:
            proofgen.write(os.path.join(out, "proof"), res.role, lane, res.chosen.low,
                           [w for _, w, _, _, _ in rows])


def run(argv):
    args = arg_parser().parse_args(argv)
    if args.cmd == "selftest":
        return selftest.main()
    path = args.spec
    try:
        with open(path) as fh:
            src = fh.read()
    except OSError as e:
        print(f"tsec: cannot read {path}: {e.strerror}", file=sys.stderr)
        return EXIT_ERRORS
    if args.cmd == "lex":
        try:
            for t in lex(src):
                print(f"{t.span} {t.kind} {t.text}")
        except TseError as e:
            show([e], src, path)
            return EXIT_ERRORS
        return EXIT_OK
    if args.cmd in ("parse", "fmt"):
        try:
            spec = parse(src)
        except Errors as e:
            show(e.errs, src, path)
            return EXIT_ERRORS
        if args.cmd == "parse":
            print(ast.dump(spec))
        else:
            sys.stdout.write(printer.spec(spec))
        return EXIT_OK
    if args.cmd == "check":
        sym = front_end(src, path, overrides_of(args.D))
        if sym is None:
            return EXIT_ERRORS
        print(f"{path}: ok, {len(sym.roles)} role(s), {len(sym.params)} param(s), "
              f"{len(sym.wires)} wire(s)")
        return EXIT_OK
    return build(args, src, path)


def main(argv=None):
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except Exception as e:      # a compiler bug: say so, exit 3
        traceback.print_exc()
        print(f"tsec: internal error: {e}", file=sys.stderr)
        return EXIT_INTERNAL

