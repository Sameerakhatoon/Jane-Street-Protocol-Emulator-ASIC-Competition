"""TSE host command line.

    python3 host/tse_cli.py [--target T] [--state FILE] CMD [ARGS] [+ CMD [ARGS] ...]

Commands (several per run, separated by +):
    load IMAGE [--no-bist] [--no-verify] [--run MASK]
    boot IMAGE FALLBACK [--no-verify] [--run MASK]
                   self-test, then IMAGE, or FALLBACK if the self-test fails
    peek ADDR [COUNT]
    poke ADDR VALUE
    status
    image build -o OUT [--clock HZ] [--name N] [--lane L=prog.s ...] [--chip REG[.I]=V ...]
                [--fallback]   (a fallback image: one program in the fallback store)
    image show IMAGE
Targets:
    model          the Python chip model (default); --state keeps it between runs,
                   --lanes 3 or 4 picks the build, --sram-dead fails its self-test
    pipe:CMD       any pin server speaking the tse_host.pinpipe protocol, e.g.
                   "pipe:python3 -m tse_host.pinpipe" or a Verilated chip harness
In load mode peek and poke use the byte bus (RD, WR); in run mode, SPI.
"""
import argparse
import json
import os
import shlex
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.dirname(HERE))

from tse_host import image, regs_map, spi  # noqa: E402
from tse_host.link import PinLink  # noqa: E402
from tse_host.model import Chip  # noqa: E402
from tse_host.session import Session, LoadError  # noqa: E402


class Target:
    def __init__(self, spec, state, seed, lanes=3, sram_dead=False):
        self.state = state
        self.pins = None
        self.chip = None
        if spec == "model":
            self.chip = Chip(seed=seed, lanes=lanes, sram_dead=sram_dead)
            if state and os.path.exists(state):
                with open(state) as fh:
                    self.chip.restore(json.load(fh))
            self.pins = self.chip
        elif spec.startswith("pipe:"):
            from tse_host.pinpipe import PipePins
            os.environ["PYTHONPATH"] = os.pathsep.join([HERE, os.environ.get("PYTHONPATH", "")])
            self.pins = PipePins(shlex.split(spec[5:]))
        else:
            raise SystemExit("unknown target %s" % spec)
        self.session = Session(PinLink(self.pins))
        self.session.load_mode = bool(self.chip is not None and not self.chip.rst_n)

    def close(self):
        if self.chip is not None and self.state:
            with open(self.state, "w") as fh:
                json.dump(self.chip.snapshot(), fh)
        if hasattr(self.pins, "close"):
            self.pins.close()


def num(s):
    return int(s, 0)


def parser():
    ap = argparse.ArgumentParser(prog="tse_cli.py", add_help=False)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("load")
    p.add_argument("image")
    p.add_argument("--no-bist", action="store_true")
    p.add_argument("--no-verify", action="store_true")
    p.add_argument("--run", type=num)
    p = sub.add_parser("boot")
    p.add_argument("image")
    p.add_argument("fallback")
    p.add_argument("--no-verify", action="store_true")
    p.add_argument("--run", type=num)
    p = sub.add_parser("peek")
    p.add_argument("addr", type=num)
    p.add_argument("count", type=num, nargs="?", default=1)
    p = sub.add_parser("poke")
    p.add_argument("addr", type=num)
    p.add_argument("value", type=num)
    sub.add_parser("status")
    p = sub.add_parser("image")
    isub = p.add_subparsers(dest="icmd", required=True)
    b = isub.add_parser("build")
    b.add_argument("-o", dest="out", required=True)
    b.add_argument("--clock", type=float, default=60e6)
    b.add_argument("--name", default="")
    b.add_argument("--lane", action="append", default=[], help="L=program.s")
    b.add_argument("--chip", action="append", default=[], help="REG[.INDEX]=VALUE")
    b.add_argument("--fallback", action="store_true")
    s = isub.add_parser("show")
    s.add_argument("image")
    return ap


def build_image(a):
    from tools.tseasm import AsmError, assemble
    lanes, chip = {}, []
    for spec in a.lane:
        lane, _, path = spec.partition("=")
        with open(path) as fh:
            try:
                lanes[int(lane)] = assemble(fh.read())
            except AsmError as e:
                raise SystemExit("%s: %s" % (path, e))
    for spec in a.chip:
        reg, _, val = spec.partition("=")
        name, _, idx = reg.partition(".")
        chip.append((name.upper(), int(idx or 0), num(val)))
    blocks = image.build_fallback(lanes, chip) if a.fallback else image.build(lanes, chip)
    name = a.name or os.path.splitext(os.path.basename(a.out))[0]
    blob = image.pack(blocks, int(a.clock), name, fallback=a.fallback)
    with open(a.out, "wb") as fh:
        fh.write(blob)
    print(image.show(blob))


def run_one(a, tgt):
    s = tgt.session if tgt else None
    if a.cmd == "image":
        if a.icmd == "build":
            build_image(a)
        else:
            with open(a.image, "rb") as fh:
                print(image.show(fh.read()))
    elif a.cmd == "load":
        with open(a.image, "rb") as fh:
            name, clock, blocks = image.unpack(fh.read())
        crc = s.load(blocks, bist=not a.no_bist, verify=not a.no_verify)
        words = sum(len(w) for _, w in blocks)
        print("loaded %s: %d words in %d blocks, clock %d Hz, crc 0x%08X"
              % (name.decode(), words, len(blocks), clock, crc))
        if a.run is not None:
            st = s.start(a.run)
            print("run mode, RUN=0x%X, STATUS=0x%08X" % (a.run, st))
    elif a.cmd == "boot":
        with open(a.image, "rb") as fh:
            name, clock, blocks = image.unpack(fh.read())
        with open(a.fallback, "rb") as fh:
            blob = fh.read()
        if not image.is_fallback(blob):
            raise image.ImageError("%s is not a fallback image" % a.fallback)
        fb_name, _, fb_blocks = image.unpack(blob)
        rep = s.boot(blocks, fb_blocks, verify=not a.no_verify)
        print("boot path %s: loaded %s, crc 0x%08X, BIST 0x%08X"
              % (rep["path"], (fb_name if rep["path"] == "fallback" else name).decode(),
                 rep["crc"], rep["bist"]))
        if a.run is not None:
            st = s.start(a.run)
            print("run mode, RUN=0x%X, STATUS=0x%08X" % (a.run, st))
    elif a.cmd == "peek":
        for i, w in enumerate(s.peek(a.addr, a.count)):
            print("0x%04X: 0x%08X" % (a.addr + i, w))
    elif a.cmd == "poke":
        s.poke(a.addr, a.value)
    elif a.cmd == "status":
        st = s.peek(regs_map.STATUS)[0]
        f = spi.decode_reg(regs_map.CHIP["STATUS"], st)
        print("mode %s, STATUS=0x%08X %s" % ("load" if s.load_mode else "run", st,
              " ".join("%s=%d" % (k, f[k]) for k in sorted(f))))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    top = argparse.ArgumentParser(prog="tse_cli.py", description=__doc__.split("\n")[0])
    top.add_argument("--target", default="model")
    top.add_argument("--state", help="model state file kept between runs")
    top.add_argument("--seed", type=int, default=0, help="power-up state of a fresh model")
    top.add_argument("--lanes", type=int, choices=(3, 4), default=3, help="model build")
    top.add_argument("--sram-dead", action="store_true", help="model SRAM returns 0")
    top.add_argument("rest", nargs=argparse.REMAINDER)
    g = top.parse_args(argv)
    groups, cur = [], []
    for tok in g.rest:
        if tok == "+":
            groups.append(cur)
            cur = []
        else:
            cur.append(tok)
    groups.append(cur)
    cmds = [parser().parse_args(x) for x in groups if x]
    if not cmds:
        top.error("no command")
    need = any(c.cmd != "image" for c in cmds)
    tgt = Target(g.target, g.state, g.seed, g.lanes, g.sram_dead) if need else None
    try:
        for c in cmds:
            run_one(c, tgt)
    except (LoadError, image.ImageError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    finally:
        if tgt:
            tgt.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
