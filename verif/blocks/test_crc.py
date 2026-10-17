"""tse_crc against model.crc on random configurations and input streams."""
from hypothesis import strategies as st

from blocks import block_test
from model.crc import Crc, CrcIn
from stims import bit, rare, stream

W32 = st.integers(0, 0xFFFFFFFF)
CFGS = st.fixed_dictionaries(dict(cfg_poly=W32, cfg_init=W32, cfg_xo=W32, cfg_check=W32,
                                  cfg_wm1=st.integers(0, 31), cfg_refin=bit, cfg_refout=bit))
STREAM = stream(dict(clear=rare(16), bit_valid=bit, bit_in=bit, byte_valid=rare(6),
                     byte_in=st.integers(0, 255), tx_load=rare(24), tx_shift=rare(4)))
OUTS = ("busy", "tx_bit", "result", "check_ok")


def build(c):
    return Crc(c["cfg_poly"], c["cfg_init"], c["cfg_xo"], c["cfg_check"], c["cfg_wm1"],
               c["cfg_refin"], c["cfg_refout"])


test_01_crc = block_test("test_01_crc", CFGS, STREAM, build, OUTS, lambda i: CrcIn(**i),
                         doc="bit and byte input, clear, TX load and shift, every output")
