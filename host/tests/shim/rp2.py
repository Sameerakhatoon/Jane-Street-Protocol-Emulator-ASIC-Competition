"""CPython stand-in for MicroPython's rp2 module, for testing tse_rp2 without a board.

asm_pio assembles the decorated function into real 16-bit PIO opcodes, the
way MicroPython does (the body runs with out, in_, pins, null and so on as
globals), so a typo or an out-of-range operand fails here as on the board.
StateMachine runs those opcodes in a small PIO simulator (OUT, IN, SET, JMP,
PULL, PUSH, MOV as NOP, delays, autopull, autopush, 4-deep FIFOs) clocked
from the simulated RP2350 system clock in WORLD, which also clocks the
Python chip model and connects GPIOs to its pins. Time only passes inside
this module: sleep_us(), FIFO waits and machine.SPI transfers advance it.
PIO.gpio_base() refuses to move the window while PIO0 holds a program, as
the RP2350 does.
"""
import types

from tse_host.model import Chip


class SimError(Exception):
    pass


class World:
    """GPIO levels, the system clock and the chip, stepped one system clock at a time."""

    def __init__(self):
        self.reset()

    def reset(self, chip=None):
        self.chip = chip or Chip(seed=0)
        self.sys_hz = 150000000
        self.clk_div = 0               # system clocks per project clock, 0: stopped
        self.t = 0                     # system clocks since start
        self.seconds = 0.0
        self.gpio_base = 0
        self.sms = []
        self.pio_out = {}              # gpio -> level written by PIO
        self.owner = {}                # gpio -> "pio" | "cpu" | "spi"
        self.cpu_out = {}              # gpio -> level from machine.Pin
        self.spi_out = {}
        self.selected = None
        self.rise_at = 0               # when a released rst_n reaches high
        self.pio_progs = set()         # programs in PIO0 instruction memory
        self.dirty = True

    def level(self, n):
        o = self.owner.get(n)
        if o == "pio":
            return self.pio_out.get(n, 0)
        if o == "spi":
            return self.spi_out.get(n, 0)
        if o == "cpu":
            return self.cpu_out.get(n, 0)
        if o == "pullup":
            return 1 if self.t >= self.rise_at else 0
        if 33 <= n <= 40:
            return (self.chip.uo_out >> (n - 33)) & 1
        return 0

    def _drive_chip(self):
        ui = 0
        for i in range(8):
            ui |= self.level(17 + i) << i
        uio = self.level(25) | self.level(26) << 1
        self.chip.set_inputs(ui_in=ui, uio_in=uio, rst_n=self.level(14))
        self.dirty = False

    def run(self, n):
        for _ in range(int(n)):
            self.t += 1
            if self.t == self.rise_at:
                self.dirty = True
            for sm in self.sms:
                if sm.on and self.t % sm.div == 0:
                    sm.step()
            if self.clk_div and self.t % self.clk_div == 0:
                if self.dirty:
                    self._drive_chip()
                self.chip.tick()
        self.seconds += n / self.sys_hz

    def run_us(self, us):
        self.run(us * self.sys_hz // 1000000)


WORLD = World()


def sleep_us(us):
    WORLD.run_us(us)


def ticks_ms():
    return int(WORLD.seconds * 1000)


def ticks_diff(a, b):
    return a - b


# ------------------------------------------------------------------ PIO
class PIO:
    OUT_LOW, OUT_HIGH, IN_LOW, IN_HIGH = 0, 1, 2, 3
    SHIFT_LEFT, SHIFT_RIGHT = 0, 1

    def __init__(self, n):
        if n != 0:
            raise SimError("only PIO(0) is simulated")

    def gpio_base(self, pin=None):
        if pin is not None:
            if pin.id not in (0, 16):
                raise ValueError("invalid GPIO base")
            if WORLD.pio_progs:
                raise OSError(22, "PIO0 holds programs")
            WORLD.gpio_base = pin.id
        return WORLD.gpio_base


class _Ins:
    def __init__(self, prog, word):
        self.prog, self.index = prog, len(prog.code)
        prog.code.append(word)

    def __getitem__(self, delay):
        if not 0 <= delay <= 31:
            raise SimError("delay %d out of range" % delay)
        self.prog.code[self.index] |= delay << 8
        return self

    def side(self, v):
        raise SimError("side-set is not used by these programs")


_OUT_DST = {"pins": 0, "x": 1, "y": 2, "null": 3, "pindirs": 4, "pc": 5, "isr": 6, "exec": 7}
_IN_SRC = {"pins": 0, "x": 1, "y": 2, "null": 3, "isr": 6, "osr": 7}
_SET_DST = {"pins": 0, "x": 1, "y": 2, "pindirs": 4}


class Program:
    def __init__(self, name, opts):
        self.name, self.opts, self.code = name, opts, []
        self.labels, self.fixups = {}, []
        self.wrap_target, self.wrap = 0, None


def _bits(n):
    if not 1 <= n <= 32:
        raise SimError("bit count %d out of range" % n)
    return n & 31


def _namespace(prog):
    def out(dst, n):
        return _Ins(prog, 0x6000 | _OUT_DST[dst] << 5 | _bits(n))

    def in_(src, n):
        return _Ins(prog, 0x4000 | _IN_SRC[src] << 5 | _bits(n))

    def set_(dst, v):
        if not 0 <= v <= 31:
            raise SimError("set value %d out of range" % v)
        return _Ins(prog, 0xE000 | _SET_DST[dst] << 5 | v)

    def nop():
        return _Ins(prog, 0xA042)

    def pull(block=True):
        return _Ins(prog, 0x8080 | (0x20 if block else 0))

    def push(block=True):
        return _Ins(prog, 0x8000 | (0x20 if block else 0))

    def jmp(label):
        ins = _Ins(prog, 0x0000)
        prog.fixups.append((ins.index, label))
        return ins

    def label(name):
        prog.labels[name] = len(prog.code)

    def wrap_target():
        prog.wrap_target = len(prog.code)

    def wrap():
        prog.wrap = len(prog.code) - 1

    ns = {"out": out, "in_": in_, "set": set_, "nop": nop, "pull": pull, "push": push,
          "jmp": jmp, "label": label, "wrap_target": wrap_target, "wrap": wrap,
          "block": True, "noblock": False}
    for k in set(_OUT_DST) | set(_IN_SRC) | set(_SET_DST):
        ns[k] = k
    return ns


def asm_pio(**opts):
    def deco(fn):
        prog = Program(fn.__name__, opts)
        types.FunctionType(fn.__code__, _namespace(prog))()
        for i, lab in prog.fixups:
            prog.code[i] |= prog.labels[lab]
        if len(prog.code) > 32:
            raise SimError("%s: %d instructions, PIO holds 32" % (prog.name, len(prog.code)))
        if prog.wrap is None:
            prog.wrap = len(prog.code) - 1
        return prog
    return deco


class StateMachine:
    def __init__(self, sm_id, prog, freq=None, out_base=None, in_base=None):
        div = WORLD.sys_hz / freq
        if div != int(div) or div < 1:
            raise SimError("PIO divider %s is not an integer" % div)
        o = prog.opts
        self.id, self.prog, self.div = sm_id, prog, int(div)
        self.out_base = out_base.id if out_base else None
        self.in_base = in_base.id if in_base else None
        for b in (self.out_base, self.in_base):
            if b is not None and not WORLD.gpio_base <= b < WORLD.gpio_base + 32:
                raise SimError("GPIO%d is outside the PIO window at %d" % (b, WORLD.gpio_base))
        self.out_right = o.get("out_shiftdir", PIO.SHIFT_LEFT) == PIO.SHIFT_RIGHT
        self.in_right = o.get("in_shiftdir", PIO.SHIFT_LEFT) == PIO.SHIFT_RIGHT
        self.autopull, self.pull_thresh = o.get("autopull", False), o.get("pull_thresh", 32)
        self.autopush, self.push_thresh = o.get("autopush", False), o.get("push_thresh", 32)
        self.pc, self.delay, self.on = 0, 0, False
        self.osr, self.osr_n, self.isr, self.isr_n = 0, 32, 0, 0
        self.x = self.y = 0
        self.tx, self.rx = [], []
        n = len(o.get("out_init", ()))
        if self.out_base is not None:
            for i in range(n):
                WORLD.owner[self.out_base + i] = "pio"
                WORLD.pio_out[self.out_base + i] = o["out_init"][i] & 1
            WORLD.dirty = True
        WORLD.pio_progs.add(id(prog))
        WORLD.sms = [s for s in WORLD.sms if s.id != sm_id] + [self]

    def active(self, v=None):
        if v is not None:
            self.on = bool(v)
        return self.on

    def put(self, w):
        guard = 0
        while len(self.tx) >= 4:
            WORLD.run(self.div)
            guard += 1
            if guard > 100000:
                raise SimError("TX FIFO never drains (state machine stopped?)")
        self.tx.append(w & 0xFFFFFFFF)

    def get(self):
        guard = 0
        while not self.rx:
            WORLD.run(self.div)
            guard += 1
            if guard > 100000:
                raise SimError("RX FIFO stays empty")
        return self.rx.pop(0)

    def tx_fifo(self):
        WORLD.run(self.div)
        return len(self.tx)

    def rx_fifo(self):
        return len(self.rx)

    def step(self):
        if self.delay:
            self.delay -= 1
            return
        word = self.prog.code[self.pc]
        op, arg, n = word >> 13, (word >> 5) & 7, word & 31 or 32
        if op == 3:                                     # OUT
            if self.autopull and self.osr_n >= self.pull_thresh:
                if not self.tx:
                    return                              # stall, pins unchanged
                self.osr, self.osr_n = self.tx.pop(0), 0
            if self.out_right:
                data = self.osr & ((1 << n) - 1)
                self.osr >>= n
            else:
                data = self.osr >> (32 - n)
                self.osr = (self.osr << n) & 0xFFFFFFFF
            self.osr_n += n
            if arg == 0:
                for i in range(n):
                    WORLD.pio_out[self.out_base + i] = (data >> i) & 1
                WORLD.dirty = True
            elif arg == 1:
                self.x = data
            elif arg == 2:
                self.y = data
            elif arg != 3:
                raise SimError("OUT destination %d not simulated" % arg)
        elif op == 2:                                   # IN
            if self.autopush and self.isr_n + n >= self.push_thresh and len(self.rx) >= 4:
                return
            if arg == 0:
                data = 0
                for i in range(n):
                    data |= WORLD.level(self.in_base + i) << i
            elif arg == 1:
                data = self.x
            elif arg == 2:
                data = self.y
            else:
                data = 0
            data &= (1 << n) - 1
            if self.in_right:
                self.isr = (self.isr >> n) | (data << (32 - n))
            else:
                self.isr = ((self.isr << n) | data) & 0xFFFFFFFF
            self.isr_n += n
            if self.autopush and self.isr_n >= self.push_thresh:
                self.rx.append(self.isr)
                self.isr, self.isr_n = 0, 0
        elif op == 4:                                   # PUSH / PULL
            if word & 0x80:
                if not self.tx:
                    if word & 0x20:
                        return
                    self.osr = self.x
                else:
                    self.osr = self.tx.pop(0)
                self.osr_n = 0
            else:
                if len(self.rx) >= 4:
                    if word & 0x20:
                        return
                else:
                    self.rx.append(self.isr)
                self.isr, self.isr_n = 0, 0
        elif op == 7:                                   # SET
            if arg == 1:
                self.x = word & 31
            elif arg == 2:
                self.y = word & 31
            else:
                raise SimError("SET destination %d not simulated" % arg)
        elif op == 0:                                   # JMP always
            self.delay = (word >> 8) & 31
            self.pc = word & 31
            return
        elif op != 5:
            raise SimError("opcode %d not simulated" % op)
        self.delay = (word >> 8) & 31
        self.pc = self.prog.wrap_target if self.pc == self.prog.wrap else self.pc + 1
