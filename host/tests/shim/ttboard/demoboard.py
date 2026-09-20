"""CPython stand-in for the Tiny Tapeout SDK DemoBoard, wired to rp2.WORLD."""
from rp2 import WORLD


class _Project:
    def __init__(self, name):
        self.name = name

    def enable(self):
        if WORLD.selected != self.name:
            WORLD.chip.power_up(WORLD.t)    # a newly selected tile powers up random
        WORLD.selected = self.name


class _Shuttle:
    def __getattr__(self, name):
        return _Project(name)


def best_sys_clock(hz, ceiling=133000000):
    """The SDK search: scan down from the ceiling for an exact even divider."""
    if hz * 2 > ceiling and hz * 2 <= 250000000:
        ceiling = hz * 2
    f = ceiling
    while f >= hz * 2:
        if f % hz == 0 and (f // hz) % 2 == 0:
            return f
        f -= 2000000 if f > 136000000 else 1000000
    raise ValueError("no exact system clock for %d Hz" % hz)


class DemoBoard:
    _one = None

    def __init__(self):
        self.shuttle = _Shuttle()

    @classmethod
    def get(cls):
        if cls._one is None:
            cls._one = cls()
        return cls._one

    def clock_project_PWM(self, hz):
        WORLD.sys_hz = best_sys_clock(int(hz))
        WORLD.clk_div = WORLD.sys_hz // int(hz)

    def reset_project(self, put_in_reset):
        WORLD.owner[14] = "cpu"
        WORLD.cpu_out[14] = 0 if put_in_reset else 1
        WORLD.dirty = True
