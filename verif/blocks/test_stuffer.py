# SPDX-License-Identifier: Apache-2.0
"""tse_stuffer against model.stuffer."""
from hypothesis import strategies as st

from blocks import block_test
from model.stuffer import Stuffer, StuffIn
from stims import often, rare, stream

STREAM = stream(dict(clear=rare(40), tx_step=often(3), tx_din=st.integers(0, 7).map(lambda v: int(v < 6)),
                     rx_step=often(3), rx_din=st.integers(0, 7).map(lambda v: int(v < 5))))
OUTS = ("tx_take", "tx_dout", "tx_pending", "rx_valid", "rx_dout", "rx_err")


def cfgs(ns):
    return st.fixed_dictionaries(dict(cfg_n=st.sampled_from(ns), cfg_mode=st.integers(0, 3)))


def build(c):
    return Stuffer(c["cfg_n"], c["cfg_mode"])


test_01_stuffer = block_test("test_01_stuffer", cfgs(range(1, 16)), STREAM, build, OUTS,
                             lambda i: StuffIn(**i), doc="n from 1 to 15, every mode")
test_02_stuffer_n0 = block_test(
    "test_02_stuffer_n0", cfgs([0]), STREAM, build, OUTS, lambda i: StuffIn(**i),
    doc="STUFF_CFG n = 0 disables stuffing: no TX bit inserted, no RX bit dropped")
