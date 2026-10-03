"""CPython stand-in for the Tiny Tapeout SDK DemoBoard, wired to rp2.WORLD.

Every object the board code reaches through the SDK (the board, a project,
the ports, rst_n) refuses public names the real SDK class does not have; the
lists come from the SDK source (host/tests/data/ttboard_sdk_names.txt).
Behaviour follows the SDK where it matters to us: enable() puts the system
clock back to the config.ini default and stops the project clock, and
reset_project(False) releases rst_n to the board pull-up instead of driving it.
"""
import os

from rp2 import WORLD
from ttboard.mode import RPMode

NAMES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data",
                          "ttboard_sdk_names.txt")
DEFAULT_SYS_HZ = 125000000      # rp_clock_frequency in the stock config.ini
RST_RISE_US = 50                # board pull-up through the reset debounce network


def sdk_names():
    names = {}
    for line in open(NAMES_FILE):
        line = line.strip()
        if line and not line.startswith("#"):
            cls, attr = line.split(".")
            names.setdefault(cls, set()).add(attr)
    return names


SDK = sdk_names()


class Checked:
    """Public attribute reads and writes must be names the real SDK class has."""
    SDK_CLASS = None

    def _known(self, name):
        if name.startswith("_"):
            return True
        return name in SDK[object.__getattribute__(self, "SDK_CLASS")]

    def _refuse(self, name):
        raise AttributeError("the SDK %s has no %r"
                             % (object.__getattribute__(self, "SDK_CLASS"), name))

    def __getattribute__(self, name):
        if not object.__getattribute__(self, "_known")(name):
            object.__getattribute__(self, "_refuse")(name)
        return object.__getattribute__(self, name)

    def __setattr__(self, name, value):
        if not self._known(name):
            self._refuse(name)
        object.__setattr__(self, name, value)


def best_sys_clock(freq, max_freq=0):
    """The system clock clock_project_PWM() picks, computed the way the SDK does."""
    if max_freq == 0:
        max_freq = 133000000
        if freq * 2 > max_freq and freq * 2 <= 250000000:
            max_freq = freq * 2
    if freq > max_freq // 2:
        raise ValueError("Requested frequency too high")
    best_freq, best_fracdiv = 0, 2000000000
    f = min(max_freq, freq * (2**24 - 1))
    f = (f // 2000000) * 2000000 if f > 136000000 else (f // 1000000) * 1000000
    while f >= 48000000 and f >= 1.9 * freq:
        nxt = f - 2000000 if f - 1000000 > 136000000 else f - 1000000
        div = max((f // (2 * freq)) * 2, 2)
        if abs(int(f / div + 0.5) - freq) > abs(int(f / (div + 2) + 0.5) - freq):
            div += 2
        fracdiv = abs(f / freq - div)
        if freq == f // div:
            return f
        if fracdiv < best_fracdiv:
            best_fracdiv, best_freq = fracdiv, f
        f = nxt
    return best_freq


class _PWM:
    def __init__(self, hz):
        self.hz = hz

    def freq(self):
        return self.hz


class _Port(Checked):
    """An 8-bit port at GPIO base..base+7; uio_oe_pico sets the direction of uio."""

    def __init__(self, sdk_class, base, oe=False):
        object.__setattr__(self, "SDK_CLASS", sdk_class)
        self._base, self._oe, self._last = base, oe, 0

    @property
    def value(self):
        if self._oe:
            return self._last
        v = 0
        for i in range(8):
            v |= WORLD.level(self._base + i) << i
        return v

    @value.setter
    def value(self, v):
        v = int(v)
        if not 0 <= v <= 0xFF:
            raise ValueError("Int value out of range for assignment of 8-bit signal")
        if self._base == 33:
            raise TypeError("uo_out has no write function")
        self._last = v
        for i in range(8):
            if self._oe:
                WORLD.owner[25 + i] = "cpu" if (v >> i) & 1 else "in"
            else:
                WORLD.cpu_out[self._base + i] = (v >> i) & 1
        WORLD.dirty = True
        WORLD.run_us(1)                 # an SDK port write takes about a microsecond


class _ResetPin(Checked):
    SDK_CLASS = "StandardPin"

    def __call__(self, v=None):
        WORLD.run_us(1)
        if v is None:
            return WORLD.level(14)
        WORLD.cpu_out[14] = 1 if v else 0
        WORLD.dirty = True


class _Project(Checked):
    SDK_CLASS = "Design"

    def __init__(self, name, board):
        self.name = name
        self._board = board

    def enable(self, force=False):
        b = self._board
        for n in range(25, 33):                     # disable(): bidirs back to inputs
            WORLD.owner[n] = "in"
        if WORLD.selected != self.name:
            WORLD.chip.power_up(WORLD.t)            # a newly selected tile powers up random
        WORLD.selected = self.name
        b.shuttle.enabled = self
        if b.mode in (RPMode.ASIC_RP_CONTROL, RPMode.ASIC_MANUAL_INPUTS):
            b._apply_user_config(self.name)
        return True


class _Shuttle(Checked):
    """Projects are attributes of the real ProjectMux too, so any name passes."""
    SDK_CLASS = "ProjectMux"

    def __init__(self, board):
        self.enabled = None
        self._board = board

    def _known(self, name):
        return True

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return _Project(name, self._board)


class DemoBoard(Checked):
    SDK_CLASS = "DemoBoard"
    _one = None

    def __init__(self):
        self._world = WORLD.chip
        self._mode = None
        self._seen = set()
        self.shuttle = _Shuttle(self)
        self.ui_in = _Port("IO", 17)
        self.uio_in = _Port("IO", 25)
        self.uo_out = _Port("IO", 33)
        self.uio_out = _Port("IO", 25)
        self.uio_oe_pico = _Port("OutputEnable", 25, oe=True)
        self.mode = RPMode.ASIC_RP_CONTROL          # the stock config.ini default
        self.shuttle.tt_um_factory_test.enable()    # and its default project

    @classmethod
    def get(cls):
        """One board per simulated world (each test resets the world)."""
        if cls._one is None or cls._one._world is not WORLD.chip:
            cls._one = cls()
        return cls._one

    @property
    def mode(self):
        return self._mode

    @mode.setter
    def mode(self, m):
        # the SDK re-creates every pin: ui_in driven, uio inputs, rst_n released
        if m != self._mode:
            self.clock_project_stop()
        self._mode = m
        for n in range(17, 33):
            WORLD.owner[n] = "cpu" if n < 25 and m == RPMode.ASIC_RP_CONTROL else "in"
        self.reset_project(False)

    @property
    def rst_n(self):
        return _ResetPin()

    def reset_project(self, put_in_reset):
        if put_in_reset:
            WORLD.owner[14] = "cpu"
            WORLD.cpu_out[14] = 0
        elif WORLD.owner.get(14) != "pullup":
            WORLD.owner[14] = "pullup"
            WORLD.rise_at = WORLD.t + RST_RISE_US * WORLD.sys_hz // 1000000
        WORLD.dirty = True

    def clock_project_PWM(self, freqHz, duty_u16=0x7FFF, quiet=False, max_rp2040_freq=0):
        hz = int(freqHz)
        WORLD.sys_hz = best_sys_clock(hz, max_rp2040_freq)
        WORLD.clk_div = WORLD.sys_hz // hz
        return _PWM(WORLD.sys_hz // WORLD.clk_div)

    def clock_project_stop(self):
        WORLD.clk_div = 0

    def _apply_user_config(self, name):
        """apply_user_config() for a project without a config.ini section."""
        if WORLD.sys_hz != DEFAULT_SYS_HZ:
            self.clock_project_stop()
            WORLD.sys_hz = DEFAULT_SYS_HZ
        self.clock_project_stop()
        if name not in self._seen:
            self._seen.add(name)
            self.reset_project(True)
            WORLD.run_us(2000)
            self.reset_project(False)
