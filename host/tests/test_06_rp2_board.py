"""The MicroPython board code, unchanged, on the CPython shim (simulated PIO, SPI, SDK)."""
import os
import random
import sys

import pytest

SHIM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shim")
if SHIM not in sys.path:
    sys.path.insert(0, SHIM)

import rp2  # noqa: E402  (the shim)
from ttboard.demoboard import best_sys_clock  # noqa: E402
from tse_host import frames as F, image, model, regs_map  # noqa: E402
from tse_host.session import FallbackNeeded  # noqa: E402
from tse_rp2 import bytebus  # noqa: E402
from tse_rp2.board import TseBoard  # noqa: E402

BLOCKS = [(0x0000, [random.Random(1).getrandbits(32) for _ in range(48)]),
          (0x1019, [0x0100]), (0x1119, [0x0201])]


@pytest.fixture
def world(tmp_path):
    rp2.WORLD.reset(model.Chip(seed=7))
    return rp2.WORLD


def write_image(tmp_path, blocks=BLOCKS, clock=60000000):
    p = tmp_path / "demo.tsei"
    p.write_bytes(image.pack(blocks, clock, b"demo"))
    return str(p)


# 1: the PIO programs assemble to real opcodes with the documented timing
def test_01_pio_programs():
    assert bytebus.loader_tx.code == [0x670A, 0x6066]       # out pins,10 [7]; out null,6
    assert bytebus.loader_rx.code == [0x6F0A, 0x6F66, 0x4008]
    cycles = lambda p: sum(1 + ((w >> 8) & 31) for w in p.code)
    assert cycles(bytebus.loader_tx) == bytebus.TX_CYCLES == 9
    assert cycles(bytebus.loader_rx) == bytebus.RX_CYCLES == 33
    assert bytebus.pio_freq(120000000) == 30000000 and bytebus.pio_freq(100000000) == 25000000


# 2: one strobe edge per entry, padding repeats the last entry, low half first
def test_02_entries():
    e = bytebus.Entries()
    words = e.words([(0x12, 1), (0x34, 0), (0x56, 0)], 2)
    assert words == [0x0312 | 0x0034 << 16, 0x0156 | 0x0156 << 16]
    assert e.words([(0x78, 0)], 4) == [0x0078 | 0x0078 << 16, 0x0078 | 0x0078 << 16]
    assert bytebus.rx_bytes([0x11223344, 0x55667788], 5) == b"\x11\x22\x33\x44\x55"


# 3: load_image on the simulated board: select, clock, self-test, load, verify, run
def test_03_load_image(world, tmp_path):
    edges = []
    chip = world.chip
    real = chip.set_inputs

    def spy(ui_in=None, uio_in=None, rst_n=None):
        if uio_in is not None and (uio_in ^ chip.uio_in) & 1:
            edges.append(chip.cycle)
        real(ui_in=ui_in, uio_in=uio_in, rst_n=rst_n)
    chip.set_inputs = spy
    name, crc, st, ms = TseBoard().load_image(write_image(tmp_path), run_mask=0b011)
    assert name == b"demo" and world.sys_hz == 120000000 and world.clk_div == 2
    assert chip.sram[:48] == BLOCKS[0][1]
    assert st & 0xF07 == 0x307                      # loaded, BIST done and passed, lanes 0 and 1 run
    assert chip.rst_n == 1 and world.owner[25] == world.owner[26] == "in"
    assert not chip.violations
    gaps = [b - a for a, b in zip(edges, edges[1:])]
    assert min(gaps) == 18                          # 9 PIO cycles at 30 MHz = 18 chip clocks


# 4: deselect and reselect: the tile powers up random, and the reload restores it
def test_04_power_down_reload(world, tmp_path):
    path = write_image(tmp_path)
    b = TseBoard()
    b.load_image(path, run_mask=1)
    getattr(b.tt.shuttle, "tt_um_other").enable()   # our tile loses power
    b.select()
    assert world.chip.sram[:48] != BLOCKS[0][1]
    world.chip.loaded = 1                           # woke up claiming to be loaded
    b.link.start_clock(60000000)
    b.link.enter_load()
    assert world.chip.loaded == 0
    b.load_image(path, run_mask=1)
    assert world.chip.sram[:48] == BLOCKS[0][1] and world.chip.running() == 1


# 5: a failing SRAM self-test raises FallbackNeeded before anything is written
def test_05_fallback(world, tmp_path):
    world.chip.bist_fail_word = 0x10
    with pytest.raises(FallbackNeeded):
        TseBoard().load_image(write_image(tmp_path))


# 6: a 50 MHz image moves the RP2350 to 100 MHz and the PIO to 25 MHz, and still loads
def test_06_fifty_mhz(world, tmp_path):
    TseBoard().load_image(write_image(tmp_path, clock=50000000))
    assert world.sys_hz == 100000000 and world.sms[0].div == 4
    assert world.chip.loaded == 1
    assert [best_sys_clock(f) for f in (60e6, 50e6, 48e6, 40e6)] == [120e6, 100e6, 96e6, 80e6]


# 7: the runtime SPI client reads status, mailboxes and drains the event FIFO
def test_07_runtime_regs(world, tmp_path):
    b = TseBoard()
    b.load_image(write_image(tmp_path), run_mask=1)
    regs = b.link.regs
    assert regs.status()["running"] == 1
    world.chip.events = [(1 << 30) | (0x3F << 24) | 2, (1 << 30) | (4 << 24) | (9 << 16) | 5]
    out, epoch = regs.events()
    assert out == [((2 << 16) | 5, 1, 4, 9)] and epoch == 2
    regs.write32(regs_map.CHIP["MBOX_TX"]["addr"] + 1, 0x1234)
    assert world.chip.mbox_tx[1] == 0x1234
    assert regs.read32(regs_map.ID) == model.ID_VALUE


# 8: a state machine outside the PIO window is refused, as on the RP2350B
def test_08_pio_window(world):
    from machine import Pin
    world.sys_hz = 120000000
    with pytest.raises(rp2.SimError):
        rp2.StateMachine(1, bytebus.loader_rx, freq=30000000, out_base=Pin(17), in_base=Pin(33))
    rp2.PIO(0).gpio_base(Pin(16))
    rp2.StateMachine(1, bytebus.loader_rx, freq=30000000, out_base=Pin(17), in_base=Pin(33))


# 9: the board streams are the library's streams: a STAT read goes through loader_rx
def test_09_same_streams(world, tmp_path):
    b = TseBoard()
    b.load_image(write_image(tmp_path))
    seen = b.link.exchange(F.rd(regs_map.STATUS))
    assert F.words_from(seen, 3, 1)[0] & 7 == 7
