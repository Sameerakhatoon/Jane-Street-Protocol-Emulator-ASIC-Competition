"""Frame roles: lowering, timing, configuration, proof checks and every rejection reason."""
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tools.tsec import frameconf, frametime, proofgen
from tools.tsec.cycles import FetchModel
from tools.tsec.diag import TseError
from tools.tsec.framelower import DATA_BASE, FrameLowering

from helpers import FCLK, front

with open("specs/eth10_tx.tse") as fh:
    ETH_TX = fh.read()
with open("specs/eth10_rx.tse") as fh:
    ETH_RX = fh.read()
with open("specs/can_rx_ack.tse") as fh:
    CAN_ACK = fh.read()
with open("specs/usb_ls_device.tse") as fh:
    USB = fh.read()
with open("tools/tsec/tests/targets/eth10_tx_window.tse") as fh:
    ETH_WIN = fh.read()

PADS = (0, 25)


def compiled(src, lane=0, **over):
    sym = front(src, **over)
    return sym, frametime.compile_role(sym, sym.roles[0], lane, FCLK, FetchModel(), 50, PADS)


def rows(res):
    """{(kind, name): [(least, most)]} of the attempt's obligation rows, from the analysis."""
    att = res.tried[0]
    out = {}
    for ins in att.prog:
        ob = getattr(ins, "ob", None)
        if ob is not None:
            s = att.an.stats[id(ins)]
            out.setdefault((ob.kind, ob.name.split(",")[0]), []).append((s.vmin, s.vmax))
    return out


def rejected(src, **over):
    """The diagnostics of a rejected frame role (lowering errors and unproven obligations)."""
    try:
        _, res = compiled(src, **over)
    except TseError as e:
        return [e]
    assert res.chosen is None
    return frametime.diags(res)


def edit(src, old, new):
    assert old in src
    return src.replace(old, new)


# ------------------------------------------------------------------ the transmitter
def test_eth10_tx_obligations():
    _, res = compiled(ETH_TX)
    assert res.chosen is not None and len(res.chosen.prog) == 78
    r = rows(res)
    assert r[("feed", "TXDATA write after TXNEED")] == [(1, 1)] * 3
    assert r[("feed", "first TXDATA write after a half-bit strobe")] == [(1, 1)]
    assert r[("hold", "hold")] == [(576, 576)]
    places = sorted(v for k, v in r.items() if k[0] == "place")
    assert places == [[(6, 6)], [(18, 18)]]


def test_eth10_tx_first_word_follows_a_half_strobe():
    _, res = compiled(ETH_TX)
    prog = res.chosen.prog
    first = next(k for k, i in enumerate(prog) if i.op == "CSRW" and "prefix word" in i.note)
    assert prog[first - 1].op == "WAIT" and prog[first - 1].a["src"] == "FLAG"
    assert first % 2 == 1                          # the WAIT shares a word with the write
    ob = prog[first].ob
    assert (ob.lo, ob.hi) == (0, 1)                # 6 cycles a bit, the half strobe 3 after the wrap


def test_eth10_tx_line_goes_idle_with_the_driver_enable():
    _, res = compiled(ETH_TX)
    on = next(i for i in res.chosen.prog if "(idle) before the frame" in i.note)
    assert on.op == "SET" and on.a["dst"] == "CMD" and on.drives == {"de": 1, "td": 0}


def test_eth10_tx_configuration():
    sym, res = compiled(ETH_TX)
    regs, pins, checks, notes = frameconf.lane_config(sym, res, FCLK)
    assert pins["td"]["vpin"] == 0 and pins["de"]["vpin"] == 1
    low = res.chosen.low
    assert low.cfg == {"txpin": 0, "tx_owns_pin": 1, "side_en": 1, "ss_base": 1, "tx_crc": 1, "idle": 1,
                       "alt_pol": 1, "alt_src": "FLAG", "alt_idx": 4}     # a host word ends the timer wait
    assert regs["DPLL_PERIOD"] == 96 and regs["DPLL_SPT"] == 0
    # CRC_INIT: the register run back over the 64 prefix bits; forward again it is the init
    reg = regs["CRC_INIT"]
    for b in low.crc_prefix:
        reg = crc_step(reg, b, 32, 0x04C11DB7)
    assert reg == 0xFFFFFFFF and len(low.crc_prefix) == 64
    assert "first word, the header" in notes["pull host fr"]


def crc_step(reg, bit, w, poly):
    """The CRC register one bit later as frameconf.unstep sees it (poly with its x^0 term)."""
    fb = (reg >> (w - 1) & 1) ^ bit
    reg = (reg & ((1 << (w - 1)) - 1)) << 1
    return reg ^ poly if fb else reg


@settings(max_examples=200, deadline=None)
@given(st.integers(0, 0xFFFFFFFF), st.integers(0, 1))
def test_crc_unstep_inverts_a_step(reg, bit):
    poly = 0x04C11DB7
    assert crc_step(frameconf.unstep(reg, bit, 32, poly), bit, 32, poly) == reg


def test_eth10_tx_without_host_ready_is_unbounded():
    errs = rejected(edit(ETH_TX, "assume host_ready : host.ready;", ""))
    assert {e.code for e in errs} == {"R316"}
    fields = dict(errs[0].fields)
    assert fields["late by"].startswith("unbounded") and "host_ready" in fields["fix"]


def test_tolerance_too_tight_is_r310():
    # TP_IDL of 310 ns is 18.6 ticks: whole cycles miss it by 0.4, more than 0.1 % allows
    errs = rejected(edit(ETH_TX, "drive td = 1 for 3 bit ~ 10 %;", "drive td = 1 for 310 ns ~ 0.1 %;"))
    assert [e.code for e in errs] == ["R310"]
    assert "18.60 +/- " in dict(errs[0].fields)["window"]


# ------------------------------------------------------------------ the buffered transmitter
def test_buffered_send_reads_the_data_window():
    sym, res = compiled(ETH_WIN)
    low = res.chosen.low
    ops = [(i.op, i.a.get("ind"), i.a.get("cond")) for i in res.chosen.prog]
    assert ("LDW", 1, None) in ops and ("JMP", None, "XNEY") in ops and ("JMP", None, "XDEC") in ops
    assert not any(i.op == "PULL" and "word" in i.note for i in res.chosen.prog)
    assert low.windows == [(0, 32)] and not low.host_ready
    regs, _, checks, notes = frameconf.lane_config(sym, res, FCLK)
    assert checks["data_window"] == "words 0 to 31"
    assert "in reverse: the last word in word 0" in notes["data window buf"]
    assert rows(res)[("feed", "TXDATA write after TXNEED")] == [(1, 1)] * 3


def test_buffered_send_on_lane_1_uses_its_own_words():
    sym = front(ETH_WIN)
    low = FrameLowering(sym, sym.roles[0], FCLK, lane=1).lower()
    assert low.windows == [(DATA_BASE, 32)]
    assert any(i.op == "SET" and i.a["dst"] == "Y" and i.a["imm"] == DATA_BASE for i in low.prog)
    assert any(i.op == "LDW" and i.a.get("addr") == DATA_BASE for i in low.prog)


def test_buffered_send_frame_larger_than_the_array_is_r308():
    errs = rejected(edit(ETH_WIN, "words[32]", "words[16]"))
    assert [e.code for e in errs] == ["R308"] and "needs up to 20 words" in errs[0].msg


def test_buffered_send_needs_the_header_first():
    errs = rejected(edit(ETH_WIN, "on host.pull(hdr) -> send;\n      on nlp.tick -> link;",
                         "on host.pull(hdr) -> link;\n      on nlp.tick -> send;"))
    assert [e.code for e in errs] == ["R300"] and "header word pulled from the host" in errs[0].msg


# ------------------------------------------------------------------ proof checks
def test_frame_checks_of_the_buffered_transmitter():
    sym, res = compiled(ETH_WIN)
    checks, skipped = proofgen.frame_checks(res.chosen, 0, FetchModel(), 50)
    kinds = sorted(c.kind for c in checks)
    assert kinds == ["window", "window", "window", "window", "window"]
    feeds = [c for c in checks if c.name == "TXDATA write after TXNEED"]
    assert [(c.lo, c.hi) for c in feeds] == [(2, 2)] * 3       # D = 1, plus 1, no extra cycles
    tp = next(c for c in checks if c.kind == "window" and c.trig.endswith("TX_ACTIVE_LO]"))
    assert (tp.lo, tp.hi, tp.tval, tp.resp, tp.rval) == (17, 19, 0, "vout[1]", 0)
    pulse = next(c for c in checks if c.kind == "window" and c.trig == "vout[1]")
    assert (pulse.lo, pulse.hi) == (6, 6)
    assert skipped == ["line 52: first TXDATA write after a half-bit strobe (its event is not on STATUS)",
                       "line 54: hold (not checked)"]
    top = proofgen.frame_harness(res.role, 0, checks, {"LANE_CFG": 0}, [0], [])
    assert top.count("tse_window_check #") == 5
    assert "assume (h_tx_we)" not in top
    assert "assume (h_tx_we)" in proofgen.frame_harness(res.role, 0, checks, {}, [0], [], True)
    sby = proofgen.frame_sby("mac_tx")
    assert "abc pdr" in sby


# ------------------------------------------------------------------ the receivers and the limits
def test_eth10_rx_reasons():
    errs = rejected(ETH_RX)
    assert errs[0].code == "R314" and "start-of-frame detection armed again" in errs[0].msg
    assert "edge(rd)" in dict(errs[0].fields)["why"]
    assert {e.code for e in errs} == {"R314", "R316", "R312"}
    late = {e.msg.split(": ", 1)[1] for e in errs if e.code == "R316"}
    assert late == {"CRC test after the end of the frame"}
    samp = [e for e in errs if e.code == "R312"]
    assert any("half-bit check" in e.msg for e in samp) and any("DPLL sample" in e.msg for e in samp)


def test_eth10_rx_reads_every_octet_in_time_and_bounds_the_crc_test():
    _, res = compiled(ETH_RX)
    r = rows(res)
    reads = r[("feed", "RXDATA read after RXFULL")]
    assert len(reads) == 5 and all(lo == 2 and hi <= 5 for lo, hi in reads)
    # bounded now, but a taken jump at the end of the frame costs more than the 6 cycles the CRC holds
    tests = r[("feed", "CRC test after the end of the frame")]
    assert all(hi is not False and 6 < hi < 16 for _, hi in tests)


GAP = ("datasheet tp_line {", "datasheet tp_gap {\n    rule ifg : line.eop -> edge(rd) >= 4.7 us;\n  }\n"
       "  datasheet tp_line {")


def test_eth10_rx_rearms_in_time_with_an_interframe_gap_rule():
    src = edit(edit(ETH_RX, *GAP), "var fr : eth;", "var fr : eth;\n    assume host_ready : host.ready;")
    _, res = compiled(src)
    rearm = [(v, ob) for ins in res.tried[0].prog for v, ob in [(res.tried[0].an.stats.get(id(ins)),
                                                                  getattr(ins, "ob", None))]
             if ob is not None and ob.kind == "rearm"]
    (st, ob), = rearm
    assert ob.hi == 270 and st.vmax <= 40 and "ifg" in ob.why
    assert "R314" not in {e.code for e in frametime.diags(res)}


def test_eth10_rx_gap_rule_without_host_ready_waits_for_the_host():
    errs = rejected(edit(ETH_RX, *GAP))
    r314 = next(e for e in errs if e.code == "R314")
    assert dict(r314.fields)["late by"].startswith("unbounded")


@pytest.mark.parametrize("level,resp", [(0, 0), (1, 1)])
def test_rule_position_write_keeps_the_answer(level, resp):
    """The program rewrites RULE0_RESP_POS with each frame's answer position; the answer bits in
    the same word must be the respond's level, not 0 (right for the CAN ACK only)."""
    src = edit(CAN_ACK, "drive txd = 0 for 1 bit;", f"drive txd = {level} for 1 bit;")
    sym = front(src)
    low = FrameLowering(sym, sym.roles[0], FCLK).lower()
    assert low.rules[0].resp == resp
    writes = [k for k, i in enumerate(low.prog) if i.op == "CSRW" and "answer position" in i.note]
    assert writes and all(low.prog[k - 1].op == "SET" and low.prog[k - 1].a["imm"] == resp for k in writes)


def test_usb_ls_device_is_a_pin_plan_limit():
    errs = rejected(USB)
    assert [e.code for e in errs] == ["R307"] and "SE0" in errs[0].msg
    notes = " ".join(errs[0].notes)
    assert "needs a CRC for 'crc5' and for 'crc16'" in notes and "no event 'sop'" in notes


@pytest.mark.parametrize("old,new,code,text", [
    ("} -> idle abort on host.pull(fr) -> send;", "} -> idle abort on nlp.tick -> send;", "R300",
     "abort on nlp.tick in a frame role"),
    ("drive td = 1 for 3 bit ~ 10 %;", "drive td = 1; drive de = 1 for 3 bit ~ 10 %;",
     "R300", "other drives at the instant a frame ends"),
    ("on host.pull(fr) -> send;\n      on nlp.tick -> link;",
     "on host.pull(fr) -> link;\n      on nlp.tick -> send;", "R300",
     "`send F(v)` needs v pulled from the host just before"),
    ("state link {                             // normal link pulse after 16 ms without a frame\n      seq {",
     "state link {\n      seq {\n        await nlp.tick;", "R300", "await nlp.tick"),
    ("assist line : manchester { pins = (td); convention = ieee; }",
     "assist line : manchester { pins = (td); convention = ieee; }\n  assist line2 : nrzi { pins = (de); }",
     "R304", "needs a line codec for 'line' and for 'line2'"),
])
def test_frame_shape_rejections(old, new, code, text):
    errs = rejected(edit(ETH_TX, old, new))
    assert errs[0].code == code and text in errs[0].msg
