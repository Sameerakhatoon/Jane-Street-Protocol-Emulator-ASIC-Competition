# SPDX-License-Identifier: Apache-2.0
"""Soundness of the state-machine timing analysis against the sequencer: host pulls that end at
once keep the environment, a pre-wait must come before the edge it prepares, a WAIT's own
condition beats its alternate, and a checked abort keeps its state until the wait again. Where
it can, a test drives the compiled program through the sequencer reference model
(verif/seqspec/seqspec_ref.py, checked against the RTL) instead of trusting hand numbers."""
import sys

import pytest

from tools.tsec import isa
from tools.tsec import fsm as F
from tools.tsec.codegen import resolved
from tools.tsec.cycles import FetchModel
from tools.tsec.fsmsched import Scheduler
from tools.tseasm import regs

from helpers import FCLK, front

sys.path.insert(0, "verif/seqspec")
import seqspec_ref as ref          # noqa: E402

with open("specs/spi_flash_target.tse") as fh:
    FLASH = fh.read()
with open("specs/spi_target.tse") as fh:
    SPI = fh.read()
with open("specs/i2c_target.tse") as fh:
    I2C = fh.read()


def compile_role(src, **over):
    sym = front(src, **over)
    return Scheduler(sym, sym.roles[0], 0, FCLK, FetchModel(), (0, 25)).compile()


def r314(tried, text):
    return [v for t in tried for v in t.violations if v.code == "R314" and text in v.msg]


# ---------------------------------------------------------------- the reference lane
def words(att):
    return [isa.encode(i.op, resolved(i, att.labels)) for i in att.prog]


def lane_cfg(att):
    f = att.fsm
    cfg = {}
    if f.jmp_pin is not None:
        cfg["jmp_pin"] = f.vin(f.jmp_pin)
    if f.alt is not None:
        cfg.update(alt_pol=f.alt[0], alt_src=f.alt[1], alt_idx=f.alt[2])
    return regs.pack("LANE_CFG", **cfg)


def run_lane(att, pins_at, ncyc):
    """Dispatch rows of the reference lane; pins_at(k) gives the lane-visible pins in cycle k."""
    env = [ref.Env(pins_at(k), 0, 0, 0, 0, 1, 0) for k in range(ncyc)]
    return ref.run(words(att), env, 0, FetchModel().lat, 0, lane_cfg(att), 0, 0, ncyc)


def issues(rows, pc):
    return [k for k, r in enumerate(rows) if r["issue"] and r["pc"] == pc]


# ---------------------------------------------------------------- 1: pulls and pre-waits
def test_flash_at_3mhz_is_rejected_for_the_missed_fall():
    att, tried = compile_role(FLASH, f_sck="3e6")
    assert att is None
    miss = r314(tried, "the wait for fall(sck) first waits")
    assert miss and all(dict(v.fields)["required"] == "reaching the wait before the earliest fall(sck) "
                        "after rise(sck)" for v in miss)
    assert any("f_sck <=" in dict(v.fields)["fix"] for v in miss)


def test_flash_at_500khz_is_accepted():
    att, _ = compile_role(FLASH, f_sck="5e5")
    assert att is not None


def test_flash_without_host_ready_is_rejected_at_any_rate():
    # the pull statement may then take any time while SCK runs on
    att, tried = compile_role(FLASH.replace("    assume host_ready : host.ready;\n", ""), f_sck="5e5")
    assert att is None and r314(tried, "the wait for fall(sck) first waits")


def test_cs_setup_too_short_for_the_first_opcode_bit():
    att, tried = compile_role(FLASH, f_sck="5e5", t_css="100e-9")
    assert att is None
    miss = r314(tried, "the wait for rise(sck) first waits")
    assert miss and "after fall(cs_n)" in dict(miss[0].fields)["required"]
    assert dict(miss[0].fields)["fix"].startswith("t_css >= 176 ns")


def jedec_first_out(f_sck, half):
    """The first answer bit of 0x9F on the reference lane, in SCK half periods after CS fell.
    The reference lane has no data path, so the decode is steered to 0x9F by comparing with 0
    (SET Y, 0 for SET Y, 159: the same instruction, the same cycles)."""
    att, tried = compile_role(FLASH, f_sck=f_sck)
    att = att or tried[0]
    w = words(att)
    dec = att.labels["decode"]
    k = next(k for k in range(dec, len(att.prog)) if att.prog[k].op == "SET"
             and att.prog[k].a.get("dst") == "Y" and att.prog[k].a.get("imm") == 0x9F)
    w[k] = isa.encode("SET", dict(resolved(att.prog[k], att.labels), imm=0))
    f = att.fsm
    cs, sck = (1 << f.pin_in["cs_n"]), (1 << f.pin_in["sck"])
    start = 40

    def pins(k):
        if k < start:
            return cs
        return sck if (k - start) // half % 2 else 0     # odd half periods have SCK high
    n = start + 2 * half * 12
    env = [ref.Env(pins(c), 0, 0, 0, 0, 1, 0) for c in range(n)]
    rows = ref.run(w, env, 0, FetchModel().lat, 0, lane_cfg(att), 0, 0, n)
    jedec = att.labels["jedec"]
    out = next(k for k in range(jedec, len(att.prog)) if att.prog[k].op == "OUT"
               and att.prog[k].a.get("dst") == "PINS")
    return (issues(rows, out)[0] - start) / half


def test_reference_lane_answers_one_period_late_at_3mhz():
    # Rise 8 (half period 15) samples the last opcode bit; the first answer bit belongs after
    # the fall at half period 16. At 3 MHz (10 cycles a half period) the lane reaches its wait
    # for SCK high after that fall, so the bit goes out after the fall at 18: one period late.
    assert 18 <= jedec_first_out("3e6", 10) < 19


def test_reference_lane_answers_in_time_at_500khz():
    assert 16 <= jedec_first_out("5e5", 60) < 17


def mosi_timing(att, css, half, nbytes, s0, gap=None):
    """For a mode 0 frame of nbytes on the reference lane (CS falls at s0, the first SCK rise
    css cycles later, SCK low for gap cycles between bytes, half by default): for each MOSI
    sample, after which rise it comes (1, 2, ...) and how many cycles after that rise."""
    f = att.fsm
    cs, sck = (1 << f.pin_in["cs_n"]), (1 << f.pin_in["sck"])
    edges, t = [], s0 + css
    for k in range(16 * nbytes):
        edges.append(t)
        t += gap if gap is not None and k % 16 == 15 else half
    end = edges[-1] + 20

    def pins(k):
        if k < s0 or k >= end:
            return cs
        return sck if sum(1 for e in edges if e <= k) % 2 else 0
    rows = run_lane(att, pins, end + 100)
    samp = {k for k, i in enumerate(att.prog) if i.op == "IN" and i.a.get("src") == "PINS"}
    rises = edges[0::2]
    out = []
    for k, r in enumerate(rows):
        if r["issue"] and r["pc"] in samp and s0 <= k < end:
            seen = [x for x in rises if x <= k]
            out.append((len(seen), k - seen[-1] if seen else None))
    return out


def mosi_samples(att, css, half, nbytes, s0, gap=None):
    return [n for n, _ in mosi_timing(att, css, half, nbytes, s0, gap)]


def no_skip(self):                          # the lowering before the byte loop skipped its pre-wait
    pass


def test_old_lowering_lost_the_first_bit_of_the_next_byte(monkeypatch):
    # Between bytes the lane used to go back to the byte loop's wait for SCK low before the
    # next rise; at 4 MHz that wait comes after the rise, so the first bit of the second byte
    # was taken after rise 10 instead of 9.
    monkeypatch.setattr(F.Fsm, "skip_known_levels", no_skip)
    att, tried = compile_role(SPI, t_css="142e-9")
    assert att is None
    miss = r314(tried, "the wait for rise(sck) first waits")
    assert any(dict(v.fields)["required"].endswith("rise(sck) after fall(sck)") for v in miss)
    got = mosi_samples(tried[0], 20, 8, 2, 100)
    assert got[:8] == list(range(1, 9)) and got[8] == 10


def test_byte_loop_goes_back_past_its_wait_for_sck_low():
    att, _ = compile_role(SPI, t_css="142e-9", f_sck="1.875e6")
    back = [i for i in att.prog if i.op == "JMP" and i.note.startswith("-> bits")]
    assert back and all("past the wait for the sck level" in i.note for i in back)
    for j in back:
        tgt = att.prog[att.labels[j.a["target"]]]
        assert tgt.op == "WAIT" and tgt.wait.kind == "edge" and tgt.a["pol"] == 1
    # the state's own entry from armed keeps it: SCK may be high when CS falls
    top = att.prog[att.labels["bits"]:]
    assert next(i for i in top if i.op == "WAIT").wait.kind == "pre"


def test_bytes_back_to_back_at_4mhz_are_sampled_late():
    # Between bytes the lane outputs the next MISO bit, tests CS, pushes and jumps back, 4
    # instructions and a jump's fetch where a byte has 2. At 4 MHz (7.5 cycles a half period)
    # the next rise can come before that is done: the first bit of the next byte is taken at
    # the right rise but up to 7 cycles after it (1 inside a byte), as SCK falls, after which
    # MOSI may change (t_mo). The window check says so (R311), no longer a missed edge.
    att, tried = compile_role(SPI, t_css="142e-9")
    assert att is None
    unrolled = next(t for t in tried if t.mode == "unrolled")
    assert {v.code for v in unrolled.violations} == {"R311"}
    assert any("shift mosi late" in v.msg for v in unrolled.violations)
    lag = [mosi_timing(unrolled, 20, 7, 2, s0) for s0 in range(100, 108)]
    assert all([n for n, _ in g] == list(range(1, 17)) for g in lag)
    assert max(g[8][1] for g in lag) == 7 and max(d for g in lag for _, d in g[:8]) == 1


def test_bytes_at_4mhz_with_a_byte_gap():
    # SCK low 11 cycles (183 ns) between bytes, which is what the analysis asks (t_gap >= 184 ns
    # as the lane sees it, with pads): the first bit of the next byte is taken 3 cycles after its rise.
    _, tried = compile_role(SPI, t_css="142e-9")
    unrolled = next(t for t in tried if t.mode == "unrolled")
    for s0 in range(100, 108):
        got = mosi_timing(unrolled, 20, 7, 3, s0, gap=11)
        assert [n for n, _ in got] == list(range(1, 25)) and max(d for _, d in got) <= 3


def test_bytes_back_to_back_at_1875khz_on_the_reference_lane():
    # t_css 142 ns is 8.5 ticks at the pins, at least 7 cycles at the lane with 25 ns of skew
    att, _ = compile_role(SPI, t_css="142e-9", f_sck="1.875e6")
    assert att is not None
    for s0 in range(100, 104):          # every slot phase of the CS fall
        assert mosi_samples(att, 7, 16, 2, s0) == list(range(1, 17))


def test_bytes_back_to_back_at_3200khz_on_the_reference_lane():
    # the fastest SCK the byte loop now follows with no gap (about 2 MHz before it skipped its
    # pre-wait); 3.2 MHz is 9.4 cycles a half period, 9 at the lane
    att, _ = compile_role(SPI, t_css="142e-9", f_sck="3.2e6")
    assert att is not None
    for s0 in range(100, 108):
        got = mosi_timing(att, 9, 9, 3, s0)
        assert [n for n, _ in got] == list(range(1, 25)) and max(d for _, d in got) < 9


# ---------------------------------------------------------------- 1b: pad skew between input pins
def test_cs_setup_counts_the_pad_skew():
    # CS and SCK come through different pads. With t_css 117 ns (7.02 ticks) and CS 25 ns slower
    # than SCK, the lane can see the first rise 5 cycles after the CS fall, not 7.
    att, tried = compile_role(SPI, t_css="117e-9", f_sck="1.875e6")
    assert att is None
    miss = r314(tried, "the wait for rise(sck) first waits")
    assert miss and any(dict(v.fields)["fix"].startswith("t_css >= 142 ns") for v in miss)
    sym = front(SPI, t_css="117e-9", f_sck="1.875e6")
    att, _ = Scheduler(sym, sym.roles[0], 0, FCLK, FetchModel(), (0, 25, 0)).compile()
    assert att is not None              # with pads known to have no skew it builds


def test_without_the_skew_the_first_bit_is_lost_on_the_reference_lane():
    # the program the analysis accepted before it counted the skew: the first rise 6 cycles
    # after the CS fall at the lane, in the slot phase where the byte loop's pre-wait comes last,
    # is missed and every byte is shifted by one bit
    sym = front(SPI, t_css="117e-9", f_sck="1.875e6")
    att, _ = Scheduler(sym, sym.roles[0], 0, FCLK, FetchModel(), (0, 25, 0)).compile()
    got = [mosi_samples(att, 6, 16, 2, s0) for s0 in range(100, 104)]
    assert list(range(1, 17)) in got and any(g[0] == 2 for g in got)


def test_cs_hold_counts_the_pad_skew():
    # The CS rise and the last SCK fall come through different pads. With t_csh 100 ns (6 ticks)
    # and CS 25 ns faster than SCK, the lane can see the CS rise 4 cycles after the last fall,
    # before it tests cs_n ahead of the push, and the finished byte is dropped.
    att, tried = compile_role(SPI, t_css="142e-9", f_sck="1.875e6", t_csh="100e-9")
    assert att is None
    miss = r314(tried, "abort seen too soon")
    assert miss and all(dict(v.fields)["obligation"].startswith("OB-HOLD rise(cs_n) after fall(sck)")
                        for v in miss)
    assert any(dict(v.fields)["fix"].startswith("t_csh >= 126 ns") for v in miss)
    sym = front(SPI, t_css="142e-9", f_sck="1.875e6", t_csh="100e-9")
    att, _ = Scheduler(sym, sym.roles[0], 0, FCLK, FetchModel(), (0, 25, 0)).compile()
    assert att is not None              # with pads known to have no skew it builds


def test_without_a_hold_rule_an_early_cs_rise_is_the_specs_abort():
    # no rule from the last fall to the CS rise: the abort may come at any time, as before
    src = SPI.replace("    rule csh      : fall(sck)  -> rise(cs_n) >= t_csh;\n", "")
    assert src != SPI
    att, _ = compile_role(src, t_css="142e-9", f_sck="1.875e6")
    assert att is not None


def byte_pushed(att, hold, s0, half=16, css=7):
    """One mode 0 byte on the reference lane, CS rising hold cycles after the last SCK fall (as
    the lane sees both): whether the lane pushes the byte."""
    f = att.fsm
    cs, sck = (1 << f.pin_in["cs_n"]), (1 << f.pin_in["sck"])
    edges = [s0 + css + half * k for k in range(16)]
    rise_cs = edges[-1] + hold

    def pins(k):
        if k < s0 or k >= rise_cs:
            return cs
        return sck if sum(1 for e in edges if e <= k) % 2 else 0
    rows = run_lane(att, pins, rise_cs + 200)
    push = [k for k, i in enumerate(att.prog) if i.op == "PUSH"]
    return any(r["issue"] and r["pc"] in push for r in rows)


@pytest.mark.parametrize("t_csh", ["142e-9", "126e-9"])      # rolled, unrolled byte loop
def test_cs_hold_on_the_reference_lane(t_csh):
    # With no hold at all the analysis names how late the cs_n test before the push can come
    # after the last fall is seen (N cycles). On the reference lane a CS rise N cycles after
    # that fall drops the byte in some slot phase, and N + 1 never does.
    att, _ = compile_role(SPI, t_css="142e-9", f_sck="1.875e6", t_csh=t_csh)
    assert att is not None
    _, tried = compile_role(SPI, t_css="142e-9", f_sck="1.875e6", t_csh="1e-9")
    msg = next(v.msg for t in tried if t.mode == att.mode for v in t.violations
               if "abort seen too soon" in v.msg)
    n = int(msg.split("comes up to ")[1].split()[0])
    assert all(byte_pushed(att, n + 1, s0) for s0 in range(100, 104))
    assert not all(byte_pushed(att, n, s0) for s0 in range(100, 104))


# ---------------------------------------------------------------- 2: own condition first
def tout_exit(self, ab):                   # the lowering before the JMP PIN exit
    self.jump(ab.target, "TOUT").note = "abort"


def test_own_condition_wins_so_a_tout_exit_loses_the_abort(monkeypatch):
    # The last wait of the bit loop is for SCK low, which holds while CS is high in mode 0: it
    # ends on its own condition with AFLAG clear and JMP TOUT falls through to the push.
    monkeypatch.setattr(F.Fsm, "level_exit", tout_exit)
    att, tried = compile_role(SPI, t_css="170e-9", f_sck="1.875e6")
    assert att is None and r314(tried, "abort may be lost")


def test_jmp_pin_exit_keeps_the_abort():
    att, _ = compile_role(SPI, t_css="170e-9", f_sck="1.875e6")
    assert att is not None
    exits = [i for i in att.prog if getattr(i, "abort_pin", False)]
    assert exits and all(i.a["cond"] == "PIN" for i in exits) and att.fsm.jmp_pin == "cs_n"


@pytest.mark.parametrize("exit_kind", ["tout", "pin"])
def test_reference_lane_after_cs_rises_mid_byte(monkeypatch, exit_kind):
    # CS rises after the third bit with SCK low and stays high: with JMP TOUT the lane pushes
    # and goes round the bit loop again, with JMP PIN it reaches idle.
    if exit_kind == "tout":
        monkeypatch.setattr(F.Fsm, "level_exit", tout_exit)
    att, tried = compile_role(SPI, t_css="170e-9", f_sck="1.875e6")
    att = att or tried[0]
    f = att.fsm
    cs, sck = (1 << f.pin_in["cs_n"]), (1 << f.pin_in["sck"])
    half, start = 16, 40
    rise_cs = start + 6 * half + 5

    def pins(k):
        if k < start or k >= rise_cs:
            return cs
        n = (k - start) // half
        return sck if n % 2 else 0
    rows = run_lane(att, pins, rise_cs + 400)
    idle = att.labels["idle"]
    push = next(k for k, i in enumerate(att.prog) if i.op == "PUSH")
    back = [k for k in issues(rows, idle) if k > rise_cs]
    pushed = [k for k in issues(rows, push) if k > rise_cs]
    if exit_kind == "tout":
        assert pushed and not back
    else:
        assert back and not pushed


# ---------------------------------------------------------------- 3: checked aborts
def test_checked_abort_runs_the_rearm_check_on_the_wait_again():
    # abort on stop needs a pin check; after a change that was a START the wait is armed again
    # and must be armed before the earliest stop after that change (fall(sda) with SCL high).
    # This spec has no rule from a START to a STOP, so the check must reject it.
    att, tried = compile_role(I2C)
    assert att is None and any(getattr(i, "rewait", False) for t in tried for i in t.prog)
    miss = [v for t in tried for v in t.violations
            if v.code == "R314" and dict(v.fields)["required"].endswith("stop after fall(sda)")]
    assert miss


def test_start_seen_by_the_alternate_is_the_last_event_on_the_wait_again():
    # With a rule from a START to a STOP the target is accepted: on the wait again the START that
    # fired the alternate is the last event, not an older one (from that the pre-wait looked late).
    with open("tools/tsec/tests/targets/i2c_target_bounded.tse") as fh:
        att, tried = compile_role(fh.read())
    assert att is not None and any(getattr(i, "rewait", False) for i in att.prog)
