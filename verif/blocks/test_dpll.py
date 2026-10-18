"""tse_dpll against model.dpll on legal configurations and jittery lines."""
from hypothesis import strategies as st

from blocks import block_test
from model.dpll import Dpll, DpllCfg, DpllIn
from stims import bit, levels, merge, rare, stream


def legal(period):
    """At least 2 cycles per bit; sample point inside the bit; SJW at most a quarter bit."""
    return st.fixed_dictionaries(dict(
        cfg_period=st.just(period), cfg_spt=st.integers(0, period - 1),
        cfg_sjw=st.integers(0, period // 4), cfg_resync=bit, cfg_dual=bit, cfg_rise=bit, cfg_fall=bit))


CFGS = st.integers(32, 16 * 24).flatmap(legal)
LINE = levels(["s_p", "s_n"], max_run=20)
STEADY = merge(LINE, stream(dict(enable=st.just(1), hard_arm=rare(30))))
TOGGLED = merge(LINE, stream(dict(enable=st.integers(0, 39).map(lambda v: int(v != 0)),
                                 hard_arm=rare(30))))
OUTS = ("bit_start", "half", "q1", "sample", "samp_val", "edge_seen")


def build(c):
    return Dpll(DpllCfg(c["cfg_period"], c["cfg_spt"], c["cfg_sjw"], c["cfg_resync"], c["cfg_dual"],
                        c["cfg_rise"], c["cfg_fall"]))


test_01_dpll = block_test("test_01_dpll", CFGS, STEADY, build, OUTS, lambda i: DpllIn(**i),
                          doc="free running, hard sync, resync with both edge senses, dual edge")
test_02_dpll_restart = block_test(
    "test_02_dpll_restart", CFGS, TOGGLED, build, OUTS, lambda i: DpllIn(**i),
    doc="turned off after a strobe and on again, the first bit gets its q1, half and sample")
