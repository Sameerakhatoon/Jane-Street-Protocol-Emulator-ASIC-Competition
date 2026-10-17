"""11: line codes: USB NRZI, Manchester conventions, decode of encode, violations."""
from hypothesis import given, settings, strategies as st

from model.codec import MAN, NRZ, NRZI, Codec, CodecIn


def encode(c, bits, half=False):
    """Line level after each step (and after each half step for Manchester)."""
    out = []
    for b in bits:
        c.step(CodecIn(tx_step=1, tx_bit=b))
        out.append(c.line)
        if half:
            c.step(CodecIn(tx_half=1))
            out.append(c.line)
    return out


def test_01_usb_nrzi_toggles_on_zero():
    c = Codec(NRZI, t_on=0)
    c.step(CodecIn(line_set=1, line_val=1))                 # J state
    assert encode(c, [0, 1, 1, 0, 0]) == [0, 0, 0, 1, 0]


def test_02_manchester_ieee_one_is_low_then_high():
    assert encode(Codec(MAN, inv=0), [1, 0], half=True) == [0, 1, 1, 0]
    assert encode(Codec(MAN, inv=1), [1, 0], half=True) == [1, 0, 0, 1]


def test_03_nrz_polarity():
    assert encode(Codec(NRZ, inv=1), [1, 0, 1]) == [0, 1, 0]


def test_04_line_set_wins_over_a_step():
    c = Codec(NRZ)
    c.step(CodecIn(tx_step=1, tx_bit=1, line_set=1, line_val=0))
    assert c.line == 0


def decode(c, line_bits, man=False):
    outs = []
    for b in line_bits:
        if man:
            outs.append(c.step(CodecIn(rx_q1=1, rx_q1_s=b[0])))
            outs.append(c.step(CodecIn(rx_sample=1, rx_s=b[1])))
        else:
            outs.append(c.step(CodecIn(rx_sample=1, rx_s=b)))
    outs.append(c.step(CodecIn()))
    return [o["rx_bit"] for o in outs if o["rx_valid"]], sum(o["rx_err"] for o in outs)


@settings(max_examples=200, deadline=None)
@given(st.integers(0, 1), st.integers(0, 1), st.integers(0, 1),
       st.lists(st.integers(0, 1), min_size=1, max_size=40))
def test_05_decode_of_encode(inv, t_on, idle, bits):
    tx = Codec(NRZI, inv, t_on)
    tx.step(CodecIn(line_set=1, line_val=idle))
    rx = Codec(NRZI, inv, t_on)
    rx.rprev = idle
    assert decode(rx, encode(tx, bits)) == (bits, 0)
    for mode in (NRZ, MAN):
        line = encode(Codec(mode, inv, t_on), bits, half=mode == MAN)
        if mode == MAN:
            line = list(zip(line[0::2], line[1::2]))
        assert decode(Codec(mode, inv, t_on), line, man=mode == MAN) == (bits, 0)


def test_06_manchester_equal_halves_are_a_violation():
    assert decode(Codec(MAN), [(1, 1), (0, 1)], man=True) == ([1, 1], 1)
