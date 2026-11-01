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
    assert dict(miss[0].fields)["fix"].startswith("t_css >= 151 ns")


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


def mosi_samples(att, css, half, nbytes, s0):
    """For a mode 0 frame of nbytes back to back on the reference lane (CS falls at s0, the
    first SCK rise css cycles later): after which rise each MOSI sample comes (1, 2, ...)."""
    f = att.fsm
    cs, sck = (1 << f.pin_in["cs_n"]), (1 << f.pin_in["sck"])
    edges = [s0 + css + half * k for k in range(16 * nbytes)]
    end = edges[-1] + 20

    def pins(k):
        if k < s0 or k >= end:
            return cs
        return sck if sum(1 for e in edges if e <= k) % 2 else 0
    rows = run_lane(att, pins, end + 100)
    samp = {k for k, i in enumerate(att.prog) if i.op == "IN" and i.a.get("src") == "PINS"}
    rises = edges[0::2]
    return [sum(1 for r in rises if r <= k) for k, r in enumerate(rows)
            if r["issue"] and r["pc"] in samp and s0 <= k < end]


def test_bytes_back_to_back_at_4mhz_are_rejected():
    # Between bytes the lane pushes and waits for SCK low again before the next rise; at
    # 4 MHz that wait comes after the rise, so the first bit of the second byte is lost.
    att, tried = compile_role(SPI, t_css="117e-9")
    assert att is None
    miss = r314(tried, "the wait for rise(sck) first waits")
    assert any(dict(v.fields)["required"].endswith("rise(sck) after fall(sck)") for v in miss)
    got = mosi_samples(tried[0], 20, 8, 2, 100)
    assert got[:8] == list(range(1, 9)) and got[8] == 10


def test_bytes_back_to_back_at_1875khz_on_the_reference_lane():
    att, _ = compile_role(SPI, t_css="117e-9", f_sck="1.875e6")
    assert att is not None
    for s0 in range(100, 104):          # every slot phase of the CS fall
        assert mosi_samples(att, 7, 16, 2, s0) == list(range(1, 17))


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
