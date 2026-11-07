# SPDX-License-Identifier: Apache-2.0
"""tse_resp_rule (K = 2, 16-bit patterns) against model.rules, both clock modes."""
from hypothesis import strategies as st

from blocks import block_test
from model.rules import Rule, Rules
from stims import bit, levels, merge, rare, stream

W16 = st.integers(0, 0xFFFF)


def rule(chain):
    pat_bits = st.sampled_from([0x0001, 0x0003, 0x0007, 0x000F, 0x00FF, 0xFFFF, 0x0000])
    ctl = st.integers(0, 0x1FFF).map(lambda v: v | 0x1000)            # enabled
    return st.fixed_dictionaries(dict(pat=W16, msk=pat_bits, pos=st.integers(0, 40), any=bit,
                                      resp=W16, ctl=ctl.map(lambda v: v | chain << 13)))


def cfgs(chain):
    return st.fixed_dictionaries(dict(r0=rule(0), r1=rule(chain), cfg_src=bit, cfg_edge=bit,
                                      cfg_tdly=st.sampled_from([0, 0, 1, 2, 5]),
                                      cfg_hold=bit)).map(ports)


def ports(c):
    r0, r1 = c["r0"], c["r1"]
    return dict(cfg_pat=r0["pat"] | r1["pat"] << 16, cfg_msk=r0["msk"] | r1["msk"] << 16,
                cfg_pos=(r0["pos"] | r0["any"] << 8) | (r1["pos"] | r1["any"] << 8) << 9,
                cfg_resp=r0["resp"] | r1["resp"] << 16,
                cfg_ctl=r0["ctl"] | r1["ctl"] << 14,
                cfg_src=c["cfg_src"], cfg_edge=c["cfg_edge"], cfg_tdly=c["cfg_tdly"], cfg_hold=c["cfg_hold"])


STREAM = merge(levels(["pin_clk", "pin_dat"], max_run=8),
               stream(dict(frame_rst=rare(60), bit_valid=rare(3), bit_in=bit, bit_start=rare(4),
                           crc_ok=st.integers(0, 3).map(lambda v: int(v != 0)), ext_flag=bit,
                           seq_wr=rare(12))))
OUTS = ("drv_en", "drv_val", "hit_flags", "hit_pulse", "abort_pulse")


class RulePorts:
    def __init__(self, c):
        rs = []
        for k in range(2):
            f = lambda name, w, lsb: (c[name] >> lsb) & ((1 << w) - 1)
            pos = f("cfg_pos", 9, 9 * k)
            ctl = f("cfg_ctl", 14, 14 * k)
            rs.append(Rule(f("cfg_pat", 16, 16 * k) | f("cfg_msk", 16, 16 * k) << 16,
                           f("cfg_resp", 16, 16 * k) | (pos & 0xFF) << 16 | (pos >> 8) << 24, ctl))
        self.r = Rules(rs, c["cfg_src"] | c["cfg_edge"] << 3 | c["cfg_tdly"] << 4
                       | c["cfg_hold"] << 12)

    def step(self, i):
        r = self.r
        o = dict(drv_en=r.drv_en, drv_val=r.drv_val, hit_flags=r.hits[0] | r.hits[1] << 1,
                 hit_pulse=r.hit_pulse, abort_pulse=r.abort_pulse)
        r.step(i["frame_rst"], i["bit_valid"], i["bit_in"], i["bit_start"], i["pin_clk"],
               i["pin_dat"], i["crc_ok"], i["ext_flag"], i["seq_wr"])
        return o


def to_rtl(i):
    i["flags_clr"] = i["frame_rst"]
    return i


def test(name, chain, doc, expect_fail=False):
    return block_test(name, cfgs(chain), STREAM.map(lambda s: [to_rtl(i) for i in s]),
                      RulePorts, OUTS, lambda i: i, expect_fail=expect_fail, doc=doc)


test_01_rules = test("test_01_rules", 0, "bit mode and pin-edge mode, tick delay, conditions, "
                     "offset, priority, saturation, hit flags, hold until the program writes")
test_02_rules_chain = test(
    "test_02_rules_chain", 1, "rule 1 with chain set matches only after a rule 0 hit in the frame")
