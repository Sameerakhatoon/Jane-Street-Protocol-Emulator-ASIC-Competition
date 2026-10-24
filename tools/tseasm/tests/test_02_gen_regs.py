"""Register table and generator: addresses, bank words, worked examples, broken tables fail loudly."""
import pytest
import yaml

from tools import gen_regs
from tools.tseasm import regs

SRC = "isa/tse_regs.yaml"


def gen_from(tmp_path, mutate):
    with open(SRC) as fh:
        src = yaml.safe_load(fh)
    mutate(src)
    p = tmp_path / "r.yaml"
    p.write_text(yaml.safe_dump(src))
    return gen_regs.load(str(p))


# 1: the committed Python table is what the generator makes from the YAML
def test_01_table_is_current():
    assert gen_regs.load(SRC) == regs.TABLE


# 2: class-L lane registers get bank words in address order, all within one 32-word bank
def test_02_bank_words():
    cfg = sorted((r["addr"], r["word"]) for r in regs.LANE.values() if r["class"] == "L")
    assert [w for _, w in cfg] == list(range(len(cfg))) and len(cfg) == regs.LANE_BANK_WORDS == 24
    assert all("word" not in r for r in regs.LANE.values() if r["class"] != "L")


# 3: worked register values (CAN, UART, I2C examples)
@pytest.mark.parametrize("reg,fields,value", [
    ("LANE_CFG", dict(rxpin=1, rx_msb=1, rx_crc=1, rx_len_m1=7, idle=1), 0x271404),   # CAN receiver
    ("LANE_CFG", dict(ss_base=2, side_en=1), 0x60),                                  # UART example
    ("RULE_CLK", dict(src=1, clk_pin=1, edge=1, tick_delay=18), 0x12B),              # I2C ACK
    ("RULE0_MATCH", dict(pattern=0xA0, mask=0xFE), 0x00FE00A0),
    ("RULE0_RESP_POS", dict(pos=7), 0x00070000),
    ("RULE0_CTL", dict(drive=1, en=1), 0x1400),
    ("RULE0_CTL", dict(offset=1, cond="CRC_GOOD", drive=1, en=1), 0x1510),            # CAN ACK
    ("CRC_CTL", dict(wm1=14), 0x0E),
    ("CRC_POLY", dict(poly=0x4599 << 17), 0x8B320000),
    ("STUFF_CFG", dict(n=6, ones=1), 0x16),
    ("STUFF_CFG", dict(n=5, ones=1, zeros=1), 0x35),
    ("CODEC_CFG", dict(mode="NRZI"), 0x01),
    ("DPLL_CTL", dict(resync=1, dual=1, fall=1), 0xB),
])
def test_03_worked_values(reg, fields, value):
    assert regs.pack(reg, **fields) == value
    assert {k: v for k, v in regs.unpack(reg, value).items() if v} == \
        {k: (regs.LANE[reg]["fields"][k]["enum"][v] if isinstance(v, str) else v)
         for k, v in fields.items() if v}


# 4: values the configuration generator must refuse
@pytest.mark.parametrize("reg,fields,what", [
    ("RULE0_RESP_POS", dict(pos=255), "outside"),
    ("RULE_CLK", dict(src=0, tick_delay=3), "tick_delay must be 0"),
    ("CODEC_CFG", dict(mode=3), "outside"),
    ("LANE_CFG", dict(txpin=4), "outside"),
    ("RULE0_CTL", dict(chain=1), "no field"),
])
def test_04_refused_values(reg, fields, what):
    with pytest.raises(regs.RegError, match=what):
        regs.pack(reg, **fields)


# 5: bits that do not exist are refused on unpack
def test_05_unpack_rejects_missing_bits():
    with pytest.raises(regs.RegError):
        regs.unpack("CRC_CTL", 0x80)
    regs.check("RULE_CLK", 0x12B)


# 6: host addresses and lane register classes
def test_06_addresses_and_classes():
    assert regs.host_addr("STATUS", lane=1) == 0x1123
    assert regs.host_addr("PAD_OUT", index=3) == 0x2003
    assert regs.host_addr("MBOX_RX", index=1) == 0x400D
    assert regs.host_addr("LANE_IN", index=7) == 0x2027
    assert regs.is_cfg(0x48) and regs.is_cfg(0x18) and not regs.is_cfg(0x20)
    assert regs.lane_writable(0x24) and not regs.lane_writable(0x23)
    assert regs.lane_readable(0x23) and not regs.lane_readable(0x18)
    assert regs.LANE["LANE_CFG"]["storage"] == "latch" and regs.LANE["TXCTL"]["storage"] == "flop"


# 7: each kind of table error stops the generator
@pytest.mark.parametrize("what,mutate", [
    ("collide", lambda s: s["lane_regs"]["TXCTL"].update(addr=0x20)),
    ("collide", lambda s: s["chip_regs"]["PIN_OPT"].update(addr=0x2027)),
    ("outside the lane window", lambda s: s["lane_regs"]["TS"].update(addr=0x100)),
    ("not in a register region", lambda s: s["chip_regs"]["RUN"].update(addr=0x5000)),
    ("overlaps another field", lambda s: s["lane_regs"]["CRC_CTL"]["fields"].update(refin=[4, 4])),
    ("outside the 32-bit word", lambda s: s["lane_regs"]["CLOCK"]["fields"].update(c=[32, 0])),
    ("does not fit", lambda s: s["lane_regs"]["RULE0_RESP_POS"]["fields"]["pos"].update(max=256)),
    ("unknown field", lambda s: s["lane_regs"]["RULE_CLK"]["require"][0]["then"].update(nope=0)),
    ("unknown keys", lambda s: s["lane_regs"]["CMD"].update(adr=1)),
    ("regions lanes and xbar overlap", lambda s: s["regions"]["xbar"].update(base=0x11F0)),
    ("bank xbar: 33 words exceed the 32-word bank", lambda s: [s["chip_regs"]["PIN_OPT"].update(bank="xbar"),
                                                            s["chip_regs"]["LANE_IN"].update(count=16)]),
    ("bank event: 33 words exceed", lambda s: [s["chip_regs"][n].update(bank="event")
                                               for n in ("PAD_OUT", "LANE_IN")]),
    ("bank lane: 33 words exceed", lambda s: [s["lane_regs"].update({f"SPARE{i}": {
        "addr": 0x50 + i, "class": "L", "fields": {"v": [0, 0]}}}) for i in range(9)]),
    ("needs a bank from chip_banks", lambda s: s["chip_regs"]["EV_CTL"].pop("bank")),
    ("needs a bank from chip_banks", lambda s: s["chip_regs"]["EV_CTL"].update(bank="nope")),
    ("only class-L registers", lambda s: s["chip_regs"]["RUN"].update(bank="event")),
    ("always use the lane bank", lambda s: s["lane_regs"]["CRC_POLY"].update(bank="xbar")),
    ("needs stride 1", lambda s: s["chip_regs"]["TRIG_WIN"].update(stride=2, count=2)),
    ("does not fit a 5-bit word address", lambda s: s.update(bank_words=64)),
])
def test_07_broken_tables_fail(tmp_path, what, mutate):
    with pytest.raises(gen_regs.GenError, match=what):
        gen_from(tmp_path, mutate)


# 8: class-L chip registers fill two chip banks in address order, none over 32 words
def test_08_chip_banks():
    assert regs.CHIP_BANKS == ["xbar", "event"] and regs.BANK_WORDS == 32
    assert regs.TABLE["bank_used"] == {"lane": 24, "xbar": 24, "event": 9}
    assert regs.chip_cfg(regs.host_addr("PAD_OUT", index=0)) == ("xbar", 0)
    assert regs.chip_cfg(regs.host_addr("LANE_IN", index=7)) == ("xbar", 23)
    assert regs.chip_cfg(0x2030) == ("event", 0)
    assert regs.chip_cfg(regs.host_addr("TRIG_MATCH", index=2)) == ("event", 4)
    assert regs.chip_cfg(0x3010) == ("event", 8)
    assert regs.chip_cfg(0x4001) is None and regs.chip_cfg(0x3001) is None
    words = sorted(regs.CHIP_CFG.values())
    assert len(words) == len(set(words)) == 33
    for b in regs.CHIP_BANKS:
        ws = [w for bb, w in words if bb == b]
        assert ws == list(range(len(ws))) and len(ws) <= regs.BANK_WORDS


# 9: chip configuration is write-only for the host, also while lanes run
def test_09_chip_configuration_write_only():
    for n in ("TRIG_ARM", "EV_CLEAR", "PAD_OUT", "LANE_IN", "PIN_OPT", "EV_CTL", "TRIG_MATCH", "TRIG_WIN", "TRIG_CTL"):
        assert regs.CHIP[n]["host_access"] == "w"
