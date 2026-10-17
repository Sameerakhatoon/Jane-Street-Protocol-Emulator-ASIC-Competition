"""tse_codec against model.codec."""
from hypothesis import strategies as st

from blocks import block_test
from model.codec import Codec, CodecIn
from stims import bit, rare, stream

CFGS = st.fixed_dictionaries(dict(cfg_mode=st.integers(0, 3), cfg_inv=bit, cfg_t_on=bit))
STREAM = stream(dict(tx_step=rare(3), tx_half=rare(3), tx_bit=bit, line_set=rare(20), line_val=bit,
                     rx_sample=rare(3), rx_s=bit, rx_q1=rare(3), rx_q1_s=bit))
OUTS = ("tx_line", "rx_valid", "rx_bit", "rx_err")

test_01_codec = block_test(
    "test_01_codec", CFGS, STREAM, lambda c: Codec(c["cfg_mode"], c["cfg_inv"], c["cfg_t_on"]),
    OUTS, lambda i: CodecIn(**i), doc="every mode and polarity, arbitrary strobes and line set")
