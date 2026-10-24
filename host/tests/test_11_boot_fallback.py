"""The fallback image variant and the boot flow that picks it when the self-test fails."""
import os
import subprocess
import sys

import pytest

from tse_host import image as I
from tse_host import model, regs_map
from tse_host.frames import crc32, be32
from tse_host.link import PinLink
from tse_host.session import Session, FallbackNeeded

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(os.path.dirname(HERE), "tse_cli.py")
FB = regs_map.REGIONS["fallback"]["base"]
FB_WORDS = regs_map.REGIONS["fallback"]["size"]
MAIN = [(0x0000, [0x01010101 * i for i in range(1, 20)]), (0x1019, [0x0000])]


class Prog:
    def __init__(self, words, start=0, config=(), window=0):
        self.words, self.start, self.config, self.window = list(words), start, list(config), window


LOOP = Prog([0x4111, 0x4011, 0x4200, 0x4010, 0x0001])


def fallback_blocks(lanes=(0,)):
    return I.build_fallback({lane: LOOP for lane in lanes}, [("PAD_OUT", 0, 0x1C0)])


def session(**kw):
    chip = model.Chip(seed=3, **kw)
    return chip, Session(PinLink(chip))


def retag(blob, magic):
    """The same image under another magic, with its CRC fixed."""
    body = magic + blob[4:-4]
    return body + be32(crc32(body))


# 1: build_fallback places one program in the store and selects it in LANE_PROG
def test_01_build_fallback():
    blocks = fallback_blocks((0, 1))
    words = dict((a + i, w) for a, ws in blocks for i, w in enumerate(ws))
    assert [words[FB + i] for i in range(3)] == [0x40114111, 0x40104200, 0xE0000001]
    lo = regs_map.LANE["LANE_PROG"]["fields"]["fallback"][1]
    assert words[0x1019] == 1 << lo and words[0x1119] == 1 << lo
    assert words[0x2000] == 0x1C0


# 2: the size limits of the fallback store: 16 instructions, start inside, one program
def test_02_store_limits():
    I.build_fallback({0: Prog(range(2 * FB_WORDS), start=2 * FB_WORDS - 1)})
    with pytest.raises(I.ImageError, match="holds"):
        I.build_fallback({0: Prog(range(2 * FB_WORDS + 1))})
    with pytest.raises(I.ImageError, match="outside"):
        I.build_fallback({0: Prog(range(4), start=2 * FB_WORDS)})
    with pytest.raises(I.ImageError, match="share"):
        I.build_fallback({0: LOOP, 1: Prog([0x4111])})


# 3: the TSEF variant round trips, and show names the kind
def test_03_round_trip():
    blocks = fallback_blocks()
    blob = I.pack(blocks, 60000000, b"fb", fallback=True)
    assert blob[:4] == b"TSEF" and I.is_fallback(blob)
    assert I.unpack(blob) == (b"fb", 60000000, blocks)
    assert "kind   fallback" in I.show(blob)
    assert not I.is_fallback(I.pack(MAIN, 60000000))


# 4: a fallback image may not touch the SRAM or the data window, overrun the store,
#    or leave a lane on the SRAM; the writer and the reader both refuse
@pytest.mark.parametrize("blocks, why", [
    ([(0x0010, [1])], "sram"),
    ([(0x0400, [1])], "data"),
    ([(FB, [0] * (FB_WORDS + 1))], "overruns"),
    ([(FB - 1, [0, 0])], "overruns"),
    ([(0x1119, [0x0100])], "lane 1"),
])
def test_04_rules(blocks, why):
    with pytest.raises(I.ImageError, match=why):
        I.pack(blocks, 60000000, fallback=True)
    with pytest.raises(I.ImageError, match=why):
        I.unpack(retag(I.pack(blocks, 60000000), b"TSEF"))


# 5: boot with a passing self-test loads the SRAM image
def test_05_boot_sram():
    chip, s = session()
    rep = s.boot(MAIN, fallback_blocks())
    assert rep["path"] == "sram" and rep["bist"] == 0
    assert chip.loaded and chip.sram[:19] == MAIN[0][1]
    assert chip.fallback == [0] * FB_WORDS


# 6: a dead macro fails the self-test; boot loads the fallback image instead, verifies
#    it, reports the BIST register, and the lane can be started from the store
def test_06_boot_dead_sram():
    chip, s = session(sram_dead=True)
    fb = fallback_blocks()
    rep = s.boot(MAIN, fb)
    assert rep["path"] == "fallback" and rep["bist"] == model.BIST_DEAD
    assert chip.loaded and chip.fallback[:3] == [0x40114111, 0x40104200, 0xE0000001]
    st = s.stat()
    assert st["loaded"] and st["bist_done"] and not st["bist_pass"]
    assert s.start(1) >> 8 & 0xF == 1


# 7: one failing cell takes the same path; load() without a fallback still raises
def test_07_boot_cell_fault():
    chip, s = session(bist_fail_word=0x42)
    rep = s.boot(MAIN, fallback_blocks())
    assert rep["path"] == "fallback" and rep["bist"] == model.BIST_CELL | 0x42
    chip, s = session(bist_fail_word=0x42)
    with pytest.raises(FallbackNeeded):
        s.load(MAIN)


# 8: an invalid fallback image is refused before the chip is touched
def test_08_boot_refuses_bad_fallback():
    chip, s = session()
    before = chip.cycle
    with pytest.raises(I.ImageError):
        s.boot(MAIN, [(0x0000, [1])])
    assert chip.cycle == before


def cli(*args):
    r = subprocess.run([sys.executable, CLI] + list(args), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


# 9: the command line builds a fallback image and boots either path on the model
def test_09_cli(tmp_path):
    src = tmp_path / "loop.s"
    src.write_text("loop: JMP ALWAYS, loop\n")
    main, fb = str(tmp_path / "m.tsei"), str(tmp_path / "f.tsei")
    cli("image", "build", "-o", main, "--lane", "0=" + str(src))
    text = cli("image", "build", "-o", fb, "--fallback", "--lane", "0=" + str(src))
    assert "kind   fallback" in text and "0x0800    1 words  fallback" in text
    assert "boot path sram: loaded m" in cli("boot", main, fb)
    text = cli("--sram-dead", "boot", main, fb, "--run", "1", "+", "status")
    assert "boot path fallback: loaded f" in text and "BIST 0x%08X" % model.BIST_DEAD in text
    assert "running=1" in text
