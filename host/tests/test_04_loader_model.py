"""The loader model driven by the stream generator: the load path and its error paths."""
import random

import pytest

from tse_host import frames as F
from tse_host import model, regs_map
from tse_host.link import PinLink
from tse_host.session import Session, LoadError, FallbackNeeded

PROG = [(0x0000, [0x10000 * i + 3 * i for i in range(40)]), (0x1019, [0x100])]


def fresh(seed=1, **kw):
    chip = model.Chip(seed=seed, **kw)
    link = PinLink(chip)
    s = Session(link)
    s.enter_load()
    return chip, link, s


def stat(link):
    return F.decode_stat(link.exchange(F.stat())[-1])


def send_load(link, blocks, mutate=None):
    stream, crc = F.load_stream(blocks)
    if mutate:
        stream = mutate(stream)
    link.exchange(stream)
    return crc


# 1: the plain load: BEGIN, bursts, COMMIT sets loaded, words land, LOAD_CRC matches
def test_01_load_commit():
    chip, link, s = fresh()
    crc = send_load(link, PROG)
    assert stat(link)["loaded"] == 1
    assert chip.sram[:40] == PROG[0][1] and chip.lane_config(0, "LANE_PROG") == 0x100
    assert s.peek(regs_map.LOAD_CRC) == [crc]
    assert s.peek(0, 40) == PROG[0][1]
    assert not chip.violations


# 2: a single wrong bit in the COMMIT CRC is refused, and BEGIN keeps loaded clear
def test_02_bad_crc():
    chip, link, s = fresh()
    send_load(link, PROG, lambda st: st[:-1] + [(st[-1][0] ^ 1, 0)])
    assert stat(link)["loaded"] == 0
    link.exchange(F.begin())
    assert stat(link)["loaded"] == 0


# 3: a truncated burst (bytes missing) leaves the CRC wrong: COMMIT refused
def test_03_truncated_frame():
    chip, link, s = fresh()
    stream, crc = F.load_stream(PROG)
    cut = stream[:30] + stream[34:]          # four data bytes of the first burst lost
    link.exchange(cut)
    assert stat(link)["loaded"] == 0


# 4: a frame byte mid-burst aborts it; the partial bytes still entered the CRC, so only
#    a fresh BEGIN and a full resend loads
def test_04_frame_abort():
    chip, link, s = fresh()
    stream, crc = F.load_stream(PROG)
    link.exchange(stream[:20] + F.stat())    # STAT aborts the WRB
    assert stat(link)["loaded"] == 0
    link.exchange(stream[1:])                # bursts and COMMIT again, without BEGIN
    assert stat(link)["loaded"] == 0
    link.exchange(stream)
    assert stat(link)["loaded"] == 1 and chip.sram[:40] == PROG[0][1]


# 5: a spurious strobe edge duplicates a byte; a glitch narrower than a clock is not seen
def test_05_spurious_strobe():
    chip, link, s = fresh()
    stream, crc = F.load_stream(PROG)
    link.exchange(stream[:12])
    link.strobe ^= 1                         # extra edge, data unchanged
    chip.set_inputs(uio_in=link.strobe | (stream[11][1] << 1))
    chip.tick(8)
    link.exchange(stream[12:])
    assert stat(link)["loaded"] == 0

    chip, link, s = fresh()
    link.exchange(stream[:12])
    chip.set_inputs(uio_in=(link.strobe ^ 1))    # strobe toggles twice between two clock edges
    chip.set_inputs(uio_in=link.strobe)
    chip.tick(8)
    link.exchange(stream[12:])
    assert stat(link)["loaded"] == 1


# 6: strobe edges closer than 4 clocks are flagged; data changing early is sampled wrong
def test_06_host_timing_faults():
    chip, link, s = fresh()
    PinLink(chip, hold=2, read_wait=2).exchange(F.begin() + F.wr(0, 5))
    assert any("apart" in v for _, v in chip.violations)
    chip, link, s = fresh()
    link.exchange(F.begin())
    stream = F.wr(0, 0x11223344)
    for i, (byte, frame) in enumerate(stream):
        link.strobe ^= 1
        chip.set_inputs(ui_in=byte, uio_in=link.strobe | frame << 1)
        chip.tick(1)
        if i >= 3:
            chip.set_inputs(ui_in=0xFF)      # data bytes change one clock after the strobe
        chip.tick(5)
    assert chip.sram[0] == 0xFFFFFFFF
    assert any("sampled" in v for _, v in chip.violations)


# 7: the first byte of each read word is stale if the host samples too early
def test_07_read_wait():
    chip, link, s = fresh()
    send_load(link, PROG)
    early = PinLink(chip, hold=6, read_wait=4)
    early.strobe = link.strobe
    seen = early.exchange(F.rd(0x0005))
    assert F.words_from(seen, 3, 1) != [PROG[0][1][5]]
    link.strobe = early.strobe
    assert s.peek(5) == [PROG[0][1][5]]


# 8: power-up: rst_n high then low clears a random loaded bit; without the high phase a
#    tile that woke up in load mode can keep it
def test_08_power_up_clears_loaded():
    for seed in range(40):
        chip = model.Chip(seed=seed)
        chip.loaded = 1
        PinLink(chip).enter_load()
        assert chip.loaded == 0
    kept = 0
    for seed in range(40):
        chip = model.Chip(seed=seed)
        chip.loaded = 1
        chip.set_inputs(rst_n=0)
        chip.tick(10)
        kept += chip.loaded
    assert kept > 0


# 9: STAT presents status on its trailing strobe, not on the command byte
def test_09_stat_needs_its_strobe():
    chip, link, s = fresh()
    send_load(link, PROG)
    chip.dout = 0
    seen = link.exchange(F.cmd(F.STAT))
    assert seen[-1] == 0
    assert link.exchange([F.BARE])[-1] & 1 == 1


# 10: BIST runs for its fixed time, destroys the SRAM, and a fault makes the session fall back
def test_10_bist():
    chip, link, s = fresh()
    link.exchange(F.begin() + F.bist())
    assert stat(link)["bist_done"] == 0
    link.wait(model.BIST_CYCLES)
    st = stat(link)
    assert st["bist_done"] and st["bist_pass"]
    assert set(chip.sram) == {model.BIST_LAST_BACKGROUND}
    chip, link, s = fresh(bist_fail_word=0x42)
    with pytest.raises(FallbackNeeded):
        s.load(PROG)
    assert s.peek(regs_map.BIST) == [(1 << 13) | 0x42]


# 11: unknown commands and bare strobes outside a command do nothing
def test_11_ignored_bytes():
    chip, link, s = fresh()
    link.exchange(F.begin())
    before = list(chip.sram)
    link.exchange([(0x55, 1), (1, 0), (2, 0), (3, 0)] + [F.BARE] * 10 + [(0xAA, 0)])
    assert chip.sram == before and stat(link)["host_err"] == 0


# 12: run mode: RUN refused until loaded, configuration of a running lane refused,
#     BEGIN refused while a lane runs; all set the sticky host error
def test_12_run_mode_rules():
    chip, link, s = fresh()
    link.exchange(F.begin())
    s.leave_load()
    s.poke(regs_map.RUN, 1)
    st = s.stat()
    assert chip.run == 0 and st["host_err"] == 1

    chip, link, s = fresh()
    s.load(PROG, bist=False)
    assert s.start(0b011) & 0xF01 == 0x301
    cfg = 0x1000 + regs_map.LANE["LANE_CFG"]["addr"]
    s.poke(cfg, 5)
    assert chip.lane_config(0, "LANE_CFG") == 0 and s.stat()["host_err"] == 1
    s.poke(0x1200 + regs_map.LANE["LANE_CFG"]["addr"], 5)     # lane 2 is stopped
    assert chip.lane_config(2, "LANE_CFG") == 5
    mosi, n = (bytes((F.BEGIN,)), 0)
    link.spi(mosi, n)
    assert chip.loaded == 1
    s.poke(regs_map.RUN, 0)
    link.spi(bytes((F.BEGIN,)), 0)
    assert chip.loaded == 0


# 13: run mode reads: event FIFO pops in order, mailbox writes allowed while running
def test_13_run_mode_reads():
    chip, link, s = fresh()
    s.load(PROG, bist=False)
    s.start(1)
    chip.events = [0x01000001, 0x02000002, 0x03000003]
    assert s.peek(regs_map.EV_STATUS)[0] & 0xF == 3
    assert s.peek(regs_map.EV_POP, 1) == [0x01000001]
    assert s.peek(regs_map.EV_POP) + s.peek(regs_map.EV_POP) == [0x02000002, 0x03000003]
    s.poke(regs_map.CHIP["MBOX_TX"]["addr"], 0xCAFE)
    assert chip.mbox_tx[0] == 0xCAFE and s.stat()["host_err"] == 0


# 14: re-entering load mode stops every lane and clears loaded before any command
def test_14_reload():
    chip, link, s = fresh()
    s.load(PROG, bist=False)
    s.start(0b111)
    assert chip.run == 0b111
    s.enter_load()
    assert chip.loaded == 0 and chip.run == 0
    rng = random.Random(5)
    other = [(0x80, [rng.getrandbits(32) for _ in range(9)])]
    s.load(other)
    assert s.peek(0x80, 9) == other[0][1]


# 15: with host_spi_off set, the SPI pins are ignored
def test_15_host_spi_off():
    chip, link, s = fresh()
    s.load(PROG + [(regs_map.PIN_OPT, [1])], bist=False)
    s.leave_load()
    assert s.peek(regs_map.ID) == [0]
