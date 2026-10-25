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

from . import ast, config, lir, printer, proofgen, report, selftest, uppaal
from .check import check
from .codegen import assemble, image_text, listing_lines
from .diag import EXIT_ERRORS, EXIT_INTERNAL, EXIT_OK, EXIT_REJECTED, Errors, TseError, render
from .lexer import lex
from .parser import parse
from .resolve import resolve
from .schedule import compile_role
from .timing import FetchModel
from .window import DATA_WORDS

LANES = 2                       # lanes in the build

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
            p.add_argument("--slots", type=int, default=4, help="slot period S, fixed by the ISA table")
            p.add_argument("--entries", type=int, default=2, help="fetch buffer words per lane")
            p.add_argument("--lat", type=int, default=1, help="fetch_latency (2: registered A_DOUT)")
            p.add_argument("--ppm", type=int, default=50, help="clock budget against the peer")
            p.add_argument("--pad-min", type=float, default=0, help="least pad delay, ns")
            p.add_argument("--pad-max", type=float, default=25, help="largest pad delay, ns")
            p.add_argument("--pad-skew", type=float, default=None,
                           help="largest delay difference between two pads, ns (default max - min)")
            p.add_argument("--emit", default="img,lst,report,json",
                           help="comma list of img, lst, report, json, lir, xml, proof, or all")
    sub.add_parser("selftest")
    return ap


def show(errs, src, path):
    for e in errs:
        print(render(e, src, path), file=sys.stderr)


def front_end(src, path, overrides=None):
    """parse, resolve, check; returns the symbol table or prints errors and returns None."""
    try:
        sym = resolve(parse(src), overrides)
        show(check(sym), src, path)
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


def fsm_rejection_diags(res):
    """The diagnostics of the state-machine strategy that came closest, with a note per other."""
    best = min(reversed(res.tried), key=lambda t: len(t.violations))
    for v in best.violations:
        for t in res.tried:
            if t is not best:
                same = any(o.code == v.code and o.span == v.span for o in t.violations)
                v.notes.append(f"with {report.STRATEGY_NAME[t.mode]}: "
                               + ("fails too" if same else "this obligation holds, but others fail"))
        if len(res.tried) > 1:
            v.notes.append(f"shown for {report.STRATEGY_NAME[best.mode]}")
    return best.violations


def rejection_diags(res):
    """One diagnostic per unproven obligation of the attempt that came closest."""
    if res.kind == "fsm":
        return fsm_rejection_diags(res)
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
    try:
        fm = FetchModel(args.slots, args.entries, args.lat)
    except ValueError as e:
        print(f"tsec: --slots {args.slots}: {e}", file=sys.stderr)
        return EXIT_ERRORS
    emit = {"img", "lst", "report", "json", "lir", "xml", "proof"} if args.emit == "all" \
        else set(args.emit.split(","))
    skew = args.pad_max - args.pad_min if args.pad_skew is None else args.pad_skew
    pads = (args.pad_min, args.pad_max, skew)
    text = report.header(sym, fclk, fm, args.ppm)
    rejected, accepted, warned = [], [], []
    wbase = 0
    for lane, role in enumerate(sym.roles):
        if lane >= LANES:
            rejected.append(TseError("R306", f"more roles than lanes: role '{role.name.text}' "
                                     f"would need lane {lane}, the chip has {LANES}", role.name.span))
            continue
        try:
            res = compile_role(sym, role, lane, fclk, fm, args.ppm, pads, (wbase, DATA_WORDS))
            if res.kind == "fsm" and res.chosen is not None:
                wbase = res.chosen.fsm.window.end
            rows = assemble(res.chosen) if res.chosen else None
            cfg = config.lane_config(sym, res, fclk) if rows is not None else None
        except TseError as e:
            text += ["", f"== role {role.name.text} ({role.kind}) on lane {lane}",
                     f"REJECTED: {e.msg} ({e.code})"]
            rejected.append(e)
            continue
        text += report.role_section(res, rows, fclk, pads)
        if res.kind == "fsm":
            for t in res.tried:
                warned += t.warnings if t is res.chosen or res.chosen is None else []
        if rows is None:
            rejected += rejection_diags(res)
        else:
            accepted.append((lane, res, rows, cfg))
    text += report.verdict(not rejected)
    print("\n".join(text))
    stem = os.path.splitext(os.path.basename(path))[0]
    show(dedup(warned), src, path)
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
        write_outputs(args.out, stem, sym, accepted, text, emit, fclk, fm, pads)
    return EXIT_OK


def dedup(errs):
    seen, out = set(), []
    for e in errs:
        if (e.code, e.span, e.msg) not in seen:
            seen.add((e.code, e.span, e.msg))
            out.append(e)
    return out


def window_rows(accepted):
    """(lane, role, Word) for every data window word an accepted role uses."""
    return [(lane, res.role.name.text, w) for lane, res, _, _ in accepted if res.kind == "fsm"
            for w in res.chosen.fsm.window.rows()]


def write_outputs(out, stem, sym, accepted, text, emit, fclk, fm=None, pads=(0, 25)):
    os.makedirs(out, exist_ok=True)

    def put(name, body):
        with open(os.path.join(out, name), "w") as fh:
            fh.write(body)
    if "img" in emit:
        put(f"{stem}.img", image_text(sym.spec.name.text,
                                      [(lane, res.role.name.text, rows) for lane, res, rows, _ in accepted]))
    if "lst" in emit:
        lst = []
        for lane, res, rows, _ in accepted:
            lst += [f"; lane {lane}: role {res.role.name.text}, "
                    f"{report.STRATEGY_NAME[res.chosen.mode]}"] + listing_lines(rows) + [""]
        put(f"{stem}.lst", "\n".join(lst))
    if "report" in emit:
        put(f"{stem}.report.txt", "\n".join(text) + "\n")
    if "json" in emit:
        lanes = [(lane, res.role, *cfg) for lane, res, _, cfg in accepted]
        put(f"{stem}.json", config.image_json(sym, lanes, fclk, window_rows(accepted)))
    if "lir" in emit:
        body = []
        for lane, res, _, _ in accepted:
            body += [f"; lane {lane}: role {res.role.name.text}"] + lir.listing(res.chosen.low.prog) + [""]
        put(f"{stem}.lir", "\n".join(body))
    if "xml" in emit:
        for _, res, _, _ in accepted:
            if res.kind == "fsm":
                continue
            put(f"{stem}_{res.role.name.text}.xml", uppaal.export(res.role, res.chosen.low))
    if "proof" in emit:
        ticks = tuple(Fr(p) * fclk / 10**9 for p in pads[:2])
        for lane, res, rows, cfg in accepted:
            if res.kind == "fsm":
                obs = proofgen.fsm_obs(sym, res.role, lane, res.chosen, fm, fclk)
                window = [(w.addr + k, w.init if k == 0 and w.init is not None else 0)
                          for w in res.chosen.fsm.window.rows() for k in range(w.size)]
            else:
                obs = proofgen.frame_obs(sym, res.role, lane, res.chosen.low, res.chosen.labels, fm)
                window = []
            if not obs:                 # nothing a response window could check
                continue
            proofgen.write(os.path.join(out, "proof"), res.role, lane, obs, cfg[0],
                           [w for _, w, _, _, _ in rows], window, ticks, res.kind != "fsm")


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

