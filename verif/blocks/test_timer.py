"""tse_timer (NCO plus down counter) against model.timer."""
from hypothesis import strategies as st

from blocks import block_test
from model.timer import Timer
from stims import bit, rare, stream

CFGS = st.fixed_dictionaries(dict(
    cfg_inc=st.one_of(st.integers(0, 1 << 24), st.integers(1 << 22, (1 << 24) - 1)).map(lambda v: v & 0xFFFFFF),
    cfg_reload=st.integers(0, 40), cfg_on_tick=bit, cfg_auto=bit))
STREAM = stream(dict(en=st.integers(0, 19).map(lambda v: int(v != 0)), start=rare(25)))
OUTS = ("tick", "expired", "count")


class TimerPorts:
    """The model timer seen through tse_timer's ports (en and start as inputs)."""
    def __init__(self, c):
        self.base = c["cfg_reload"] | c["cfg_on_tick"] << 16
        self.t = Timer(c["cfg_inc"], self.base, auto=bool(c["cfg_auto"]))

    def step(self, i):
        t = self.t
        o = dict(tick=t.tick, expired=t.expired, count=t.count)
        t.ctl = self.base | i["en"] << 17
        t.step(frame_rst=i["start"])
        return o


test_01_timer = block_test("test_01_timer", CFGS, STREAM, TimerPorts, OUTS, lambda i: i,
                           doc="NCO tick, cycle and tick counting, start, auto reload, enable")
