"""09: the CRC unit against the catalogue and against the textbook definition."""
from hypothesis import given, settings, strategies as st

from model.crc import Crc, CrcIn, crc_textbook, left, rev

MSG = b"123456789"
# name: (width, poly, init, refin, refout, xorout, check, residue)
CATALOGUE = {
    "CRC-32/ISO-HDLC": (32, 0x04C11DB7, 0xFFFFFFFF, 1, 1, 0xFFFFFFFF, 0xCBF43926, 0xDEBB20E3),
    "CRC-32/BZIP2": (32, 0x04C11DB7, 0xFFFFFFFF, 0, 0, 0xFFFFFFFF, 0xFC891918, 0xC704DD7B),
    "CRC-16/ARC": (16, 0x8005, 0x0000, 1, 1, 0x0000, 0xBB3D, 0x0000),
    "CRC-16/IBM-3740": (16, 0x1021, 0xFFFF, 0, 0, 0x0000, 0x29B1, 0x0000),
    "CRC-16/USB": (16, 0x8005, 0xFFFF, 1, 1, 0xFFFF, 0xB4C8, 0xB001),
    "CRC-15/CAN": (15, 0x4599, 0x0000, 0, 0, 0x0000, 0x059E, 0x0000),
    "CRC-5/USB": (5, 0x05, 0x1F, 1, 1, 0x1F, 0x19, 0x06),
    "CRC-8/SMBUS": (8, 0x07, 0x00, 0, 0, 0x00, 0xF4, 0x00),
    "CRC-17/CAN-FD": (17, 0x1685B, 0x00000, 0, 0, 0x00000, 0x04F03, 0x00000),
}


def feed_bytes(c, data):
    for b in data:
        c.step(CrcIn(byte_valid=1, byte_in=b))
        while c.out()["busy"]:
            c.step(CrcIn())


def feed_bits(c, bits):
    for b in bits:
        c.step(CrcIn(bit_valid=1, bit_in=b))


def test_01_textbook_reproduces_the_catalogue():
    for name, (w, p, i, ri, ro, x, check, _) in CATALOGUE.items():
        assert crc_textbook(MSG, w, p, i, ri, ro, x) == check, name


def test_02_byte_input_gives_the_check_value():
    for name, (w, p, i, ri, ro, x, check, _) in CATALOGUE.items():
        c = Crc.catalogue(w, p, i, ri, ro, x)
        c.step(CrcIn(clear=1))
        feed_bytes(c, MSG)
        assert c.out()["result"] == check, name


def test_03_byte_takes_eight_cycles_and_busy_follows():
    c = Crc.catalogue(16, 0x8005, 0xFFFF, 1, 1, 0xFFFF)
    c.step(CrcIn(clear=1))
    c.step(CrcIn(byte_valid=1, byte_in=0x31))
    busy = []
    for _ in range(10):
        busy.append(c.out()["busy"])
        c.step(CrcIn())
    assert busy == [1] * 8 + [0, 0]


def test_04_message_plus_crc_leaves_the_residue_and_check_ok():
    """Unreflected CRCs: appending the CRC bits MSB first leaves the residue."""
    for name, (w, p, i, ri, ro, x, check, residue) in CATALOGUE.items():
        if ri or ro:
            continue
        c = Crc.catalogue(w, p, i, ri, ro, x, check=residue ^ x)
        c.step(CrcIn(clear=1))
        feed_bytes(c, MSG)
        feed_bits(c, [(check >> k) & 1 for k in range(w - 1, -1, -1)])
        assert c.out()["check_ok"] == 1, name


def test_05_tx_load_and_shift_send_the_crc_in_order():
    w, p, i, x = 15, 0x4599, 0, 0
    c = Crc.catalogue(w, p, i, 0, 0, x)
    c.step(CrcIn(clear=1))
    feed_bytes(c, MSG)
    c.step(CrcIn(tx_load=1))
    sent = []
    for _ in range(w):
        sent.append(c.out()["tx_bit"])
        c.step(CrcIn(tx_shift=1))
    assert int("".join(map(str, sent)), 2) == 0x059E


def test_06_clear_wins_over_a_bit():
    c = Crc(poly=left(0x07, 8), init=left(0xA5, 8), wm1=7)
    c.step(CrcIn(clear=1, bit_valid=1, bit_in=1))
    assert c.out()["result"] == 0xA5


@settings(max_examples=200, deadline=None)
@given(st.integers(5, 32).flatmap(lambda w: st.tuples(
    st.just(w), st.integers(0, (1 << w) - 1), st.integers(0, (1 << w) - 1),
    st.integers(0, (1 << w) - 1), st.integers(0, 1), st.integers(0, 1))),
    st.binary(min_size=0, max_size=12), st.booleans())
def test_07_every_configuration_matches_the_textbook(cfg, data, bitwise):
    w, p, i, x, ri, ro = cfg
    c = Crc.catalogue(w, p, i, ri, ro, x)
    c.step(CrcIn(clear=1))
    if bitwise:
        for b in data:
            b = rev(b, 8) if ri else b
            feed_bits(c, [(b >> k) & 1 for k in range(7, -1, -1)])
    else:
        feed_bytes(c, data)
    assert c.out()["result"] == crc_textbook(data, w, p, i, ri, ro, x)
