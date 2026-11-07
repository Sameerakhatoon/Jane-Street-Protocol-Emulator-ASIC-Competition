# SPDX-License-Identifier: Apache-2.0
"""06: response rules in bit mode and pin-edge mode."""
from model.rules import Rule, Rules

P = 10                                     # bit period in cycles for bit-mode streams


def bit_stream(rules, bits, crc_ok=1):
    """BITSTART at b * P, the recovered bit at b * P + 7. Returns drv_en per cycle."""
    drv = []
    for c in range(len(bits) * P + 2 * P):
        b, ph = divmod(c, P)
        bv = int(ph == 7 and b < len(bits))
        rules.step(frame_rst=int(c == 0), bit_valid=bv, bit_in=bits[b] if bv else 0,
                   bit_start=int(ph == 0), crc_ok=crc_ok)
        drv.append((rules.drv_en, rules.drv_val))
    return drv


def test_can_ack_in_the_bit_after_the_delimiter():
    n = 8
    pos = 34 + 8 * n
    bits = [0] * pos + [1] + [1] * 5
    r = Rules([Rule(0x00010001, pos << 16, 0x1510), Rule()])
    drv = bit_stream(r, bits)
    on = [c for c, (en, _) in enumerate(drv) if en]
    assert on[0] == (pos + 1) * P and on[-1] == (pos + 2) * P - 1
    assert all(v == 0 for en, v in drv if en)


def test_no_ack_when_crc_bad():
    pos = 20
    r = Rules([Rule(0x00010001, pos << 16, 0x1510), Rule()])
    assert not any(en for en, _ in bit_stream(r, [0] * pos + [1, 1], crc_ok=0))


def test_counter_saturates_at_255():
    r = Rules()
    bit_stream(r, [1] * 300)
    assert r.cnt == 255


def test_rule0_wins_and_both_hit_flags_set():
    r = Rules([Rule(0x00010001, 1 << 24, 0x1400 | 0x0), Rule(0x00010001, (1 << 24) | 1, 0x1400)])
    drv = bit_stream(r, [0, 0, 1, 0])
    assert r.hits == [1, 1]
    assert all(v == 0 for en, v in drv if en)     # rule 0's response (0), not rule 1's (1)


def test_chain_needs_rule0_earlier_in_frame():
    r0 = Rule(0x00000000, 5 << 16, 0x1000)          # hit at bit 5, no drive
    r1 = Rule(0x00010001, 1 << 24, 0x3000)          # any 1 bit, chained
    r = Rules([r0, r1])
    hits1 = []
    for b in range(10):
        bit_stream_one(r, b, 1)
        hits1.append(r.hits[1])
    assert hits1[:6] == [0] * 6 and hits1[6] == 1   # "earlier": the bit after rule 0 hit


def bit_stream_one(r, b, bit):
    for ph in range(P):
        r.step(frame_rst=int(b == 0 and ph == 0), bit_valid=int(ph == 7), bit_in=bit,
               bit_start=int(ph == 0))


def test_i2c_ack_pin_edge_mode_timing():
    # I2C target ACK: SCL on vpin 1 falling, tick_delay 18, address 0x50 write
    r = Rules([Rule(0x00FE00A0, 0x00070000, 0x1400), Rule()], rule_clk=0x12B)
    byte = [1, 0, 1, 0, 0, 0, 0, 0, 1, 1]
    drv = []
    for c in range(10 * 40):
        j = c // 40
        scl = int(c % 40 < 20)
        sda = byte[min(j + (1 if c % 40 >= 30 else 0), 9)]
        r.step(frame_rst=int(c == 0), pin_clk=scl, pin_dat=sda)
        drv.append(r.drv_en)
    e7, e8 = 7 * 40 + 20, 8 * 40 + 20             # 8th and 9th falling edges, detected
    on = [c + 1 for c, en in enumerate(drv) if en]  # cycles in which the drive register is 1
    assert on[0] == e7 + 18 + 1 and on[-1] == e8 + 18
