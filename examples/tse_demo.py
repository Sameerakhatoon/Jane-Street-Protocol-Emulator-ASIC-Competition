"""Shared pieces of the end-to-end examples.

A demo goes: tsec output (NAME.img, NAME.json) -> image blocks with the pin
choices of the demo -> Session.load over the pin pipe of the Verilated chip
-> RUN -> host traffic over SPI while scheduled waveforms drive some input
pins -> the pins-only VCD that the harness writes -> the judges.

The compiler does not place wires on pads (it names a virtual pin per wire),
so each demo says which pad a wire uses; this module turns that into
PAD_OUT and LANE_IN writes.

DrivenPins sits between PinLink and the pin pipe: the link owns the pins it
sets, and a few input bits belong to scheduled waveforms (a UART line, an
I2C target). Time is split at every scheduled edge, so a waveform lands on
exact model cycles while SPI transactions run. An attached bus model (an
open-drain I2C bus with its target) sleeps until the chip moves one of its
lines, through the U line of the pin server.
"""
import json
import os
import shlex
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "host"))
sys.path.insert(1, ROOT)

from tse_host import image, regs_map  # noqa: E402
from tse_host.link import PinLink  # noqa: E402
from tse_host.pinpipe import PipePins  # noqa: E402
from tse_host.session import Session  # noqa: E402
from tools.tsec import isa  # noqa: E402

FCLK = 60000000
WINDOW_INSNS = 2 * image.WINDOW_WORDS
UI, UIO = "ui", "uio"


class Program:
    def __init__(self, window, words, config, start=0):
        self.window = window
        self.start = start
        self.words = words
        self.config = config


def _sets_dirs(w):
    d = isa.decode(w)
    return d is not None and d[0] == "SET" and d[1]["dst"] == "PINDIRS"


def output_prologue(words, pins):
    """(words, start): when a program never sets pin directions (the paced
    frame lowering leaves them at reset, all inputs, so an output pad would
    stay idle), append SET PINS to the idle levels, SET PINDIRS for its
    outputs and a jump to 0, and start the lane there. The program itself
    is unchanged word for word."""
    mask = idle = 0
    for p in pins.values():
        if "vpin_out" in p:
            mask |= 1 << p["vpin_out"]
            if int(p["PAD_OUT"], 16) >> 7 & 1:
                idle |= 1 << p["vpin_out"]
    if not mask or any(_sets_dirs(w) for w in words):
        return words, 0
    pro = [isa.encode("SET", dict(ss=0, dst="PINS", imm=mask << 4 | idle)),
           isa.encode("SET", dict(ss=0, dst="PINDIRS", imm=mask << 4 | mask)),
           isa.encode("JMP", dict(ss=0, cond="ALWAYS", target=0))]
    return words + pro, len(words)


def read_tsec(outdir, name):
    """Lanes of a tsec build: {lane: (role, words, regs, pins)}."""
    words = {}
    addr = 0
    with open(os.path.join(outdir, name + ".img")) as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln or ln.startswith("//"):
                continue
            if ln.startswith("@"):
                addr = int(ln[1:], 16)
                continue
            words.setdefault(addr // WINDOW_INSNS, []).append(int(ln, 16))
            addr += 1
    with open(os.path.join(outdir, name + ".json")) as fh:
        meta = json.load(fh)
    if meta["fclk_hz"] != FCLK:
        raise SystemExit("image built for %d Hz, the harness runs %d Hz" % (meta["fclk_hz"], FCLK))
    lanes = {}
    for ln in meta["lanes"]:
        regs = [(k, int(v, 16)) for k, v in ln["regs"].items()]
        lanes[ln["lane"]] = (ln["role"], words[ln["lane"]], regs, ln["pins"])
    return lanes


def move_lane(lanes, old, new):
    """{new: lane} from a one-spec build: tsec places each spec from lane 0,
    so a second spec on the chip moves; the PAD_OUT source follows."""
    role, words, regs, pins = lanes[old]
    moved = {}
    for wire, p in pins.items():
        q = dict(p)
        if "PAD_OUT" in q:
            v = int(q["PAD_OUT"], 16)
            q["PAD_OUT"] = "0x%08X" % (v & ~0xF | (new * 4 + p["vpin_out"]))
        moved[wire] = q
    return {new: (role, words, regs, moved)}


def pin_source(reg, bit):
    """LANE_IN source number of an input pin."""
    return bit if reg == UI else 8 + bit


def build_blocks(lanes, pads, inputs, extra=()):
    """Image blocks. pads: {wire: output pad (0..7 uo_out, 8..15 uio)};
    inputs: {wire: (UI or UIO, bit)}; extra: more (lane, register, value)."""
    progs = {}
    chip = []
    for lane, (role, words, regs, pins) in lanes.items():
        cfg = list(regs) + [(r, v) for l, r, v in extra if l == lane]
        prog, start = output_prologue(words, pins)
        progs[lane] = Program(lane, prog, cfg, start)
        for wire, p in pins.items():
            if "PAD_OUT" in p:
                chip.append(("PAD_OUT", pads[wire], int(p["PAD_OUT"], 16)))
            if "vpin_in" in p:
                reg, bit = inputs[wire]
                chip.append(("LANE_IN", lane * 4 + p["vpin_in"], pin_source(reg, bit)))
    return image.build(progs, chip)


class BusPipePins(PipePins):
    """The pin pipe plus the U line: run until the chip moves a uio line."""

    def run_until(self, n, mask):
        """(cycles run, uo_out, uio_out, uio_oe)."""
        self.buf.append("U %x %x" % (n, mask))
        self.proc.stdin.write("\n".join(self.buf) + "\n")
        self.proc.stdin.flush()
        self.buf = []
        line = self.proc.stdout.readline()
        if not line:
            raise IOError("pin server closed the pipe")
        return tuple(int(x, 16) for x in line.split())


class DrivenPins:
    def __init__(self, pins):
        self.pins = pins
        self.cycle = 0
        self.host = {UI: 0, UIO: 0}
        self.own = {UI: {}, UIO: {}}        # bit -> level
        self.events = []                    # sorted (cycle, reg, bit, level)
        self.rst_n = 1
        self.bus = None                     # a host-side open-drain bus model, see attach()

    def attach(self, bus):
        """bus: mask (uio bits it watches), next_due() (cycle of its next own
        action or None) and update(pins, uio_out, uio_oe), which sets the
        owned bits. Time then runs until the chip moves a watched line or
        the bus has something to do, so the bus sees every edge exactly."""
        self.bus = bus
        _, _, out, oe = self.pins.run_until(0, bus.mask)
        self.bus_seen = (out, oe)
        bus.update(self, out, oe)

    def _push(self):
        v = {}
        for reg in (UI, UIO):
            x = self.host[reg]
            for bit, lvl in self.own[reg].items():
                x = x & ~(1 << bit) | lvl << bit
            v[reg] = x
        self.pins.set_inputs(ui_in=v[UI], uio_in=v[UIO], rst_n=self.rst_n)

    def hold(self, reg, bit, level):
        self.own[reg][bit] = level
        self._push()

    def schedule(self, reg, bit, edges):
        """edges: [(cycle, level)]; the bit is owned from now on."""
        self.own[reg].setdefault(bit, 1)
        self.events = sorted(self.events + [(c, reg, bit, v) for c, v in edges])

    def busy_until(self):
        return self.events[-1][0] if self.events else self.cycle

    def set_inputs(self, ui_in=None, uio_in=None, rst_n=None):
        if ui_in is not None:
            self.host[UI] = ui_in & 0xFF
        if uio_in is not None:
            self.host[UIO] = uio_in & 0xFF
        if rst_n is not None:
            self.rst_n = 1 if rst_n else 0
        self._push()

    def tick(self, n=1):
        if self.bus is not None:
            end = self.cycle + n
            while self.cycle < end:
                k = end - self.cycle
                due = self.bus.next_due()
                if due is not None and due <= self.cycle:
                    self.bus.update(self, *self.bus_seen)
                    continue
                if due is not None:
                    k = min(k, due - self.cycle)
                ran, _, out, oe = self.pins.run_until(k, self.bus.mask)
                self.cycle += ran
                self.bus_seen = (out, oe)
                self.bus.update(self, out, oe)
            return
        end = self.cycle + n
        while self.cycle < end:
            changed = False
            while self.events and self.events[0][0] <= self.cycle:
                _, reg, bit, lvl = self.events.pop(0)
                self.own[reg][bit] = lvl
                changed = True
            if changed:
                self._push()
            step = end - self.cycle
            if self.events:
                step = min(step, self.events[0][0] - self.cycle)
            self.pins.tick(step)
            self.cycle += step
        while self.events and self.events[0][0] <= self.cycle:
            _, reg, bit, lvl = self.events.pop(0)
            self.own[reg][bit] = lvl
            self._push()

    @property
    def uo_out(self):
        return self.pins.uo_out

    def close(self):
        self.pins.close()


def uart_edges(start, data, baud, gap_bits=0):
    """8N1 line levels for the bytes of data from cycle start, back to back
    plus gap_bits idle bits; every bit edge rounds the exact bit time."""
    edges = []
    bit = FCLK / baud
    k = 0.0
    for b in data:
        frame = [0] + [(b >> i) & 1 for i in range(8)] + [1]
        for i, v in enumerate(frame):
            edges.append((start + int(round(k + i * bit)), v))
        k += (10 + gap_bits) * bit
    return edges


class Chip:
    """The Verilated chip behind the pin pipe, with a session on top."""

    def __init__(self, sim_cmd):
        self.pins = DrivenPins(BusPipePins(shlex.split(sim_cmd)))
        self.session = Session(PinLink(self.pins))

    def load(self, blocks):
        return self.session.load(blocks)

    def start(self, mask):
        return self.session.start(mask)

    def peek(self, addr):
        return self.session.peek(addr)[0]

    def poke(self, addr, value):
        self.session.poke(addr, value)

    def wait(self, cycles):
        self.pins.tick(cycles)

    def close(self):
        self.pins.close()


def chip_reg(name, index=0):
    return image.chip_addr(name, index)


def status_fields(word):
    return {k: (word >> lo) & ((1 << (hi - lo + 1)) - 1)
            for k, (hi, lo) in regs_map.CHIP["STATUS"]["fields"].items()}
