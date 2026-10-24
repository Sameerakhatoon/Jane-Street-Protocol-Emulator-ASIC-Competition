"""cocotb: the load-mode byte bus of the whole chip, driven with the host
library's streams (host/tse_host/frames.py): random images load and read
back, the COMMIT CRC, bad CRC, a corrupted byte, a truncated frame,
spurious strobes, unknown commands, loaded cleared on load-mode entry, the
self-test, and RUN refused before COMMIT.
"""
import os
import random

import cocotb

from tse_host import frames as F
from tools.tseasm import regs
from chip_pins import Pins

LANES = int(os.environ.get("LANES", "2"))
LOADED, BIST_DONE, BIST_PASS, HOST_ERR = 0x01, 0x02, 0x04, 0x08


def image(rng):
    """(addr, words) blocks: two SRAM ranges, lane CRC words, every chip config word."""
    blocks = [(0x0000, [rng.getrandbits(32) for _ in range(40)]),
              (0x0100, [rng.getrandbits(32) for _ in range(20)])]
    for lane in range(LANES):
        blocks.append((regs.host_addr("CRC_POLY", lane=lane), [rng.getrandbits(32) for _ in range(4)]))
    for name, r in regs.CHIP.items():
        if r["class"] != "L":
            continue
        count = 4 * LANES if name == "LANE_IN" else r["count"]
        blocks.append((r["addr"], [rng.getrandbits(32) & r["mask"] for _ in range(count)]))
    return blocks


async def begin(p):
    await p.exchange(F.begin())


# chip configuration is write-only for the host: it reads back as 0
CHIP_CFG = {r["addr"] + i for r in regs.CHIP.values() if r["class"] == "L" for i in range(r["count"])}


async def check_readback(p, blocks):
    stream, where = F.readback(blocks)
    seen = await p.exchange(stream)
    for addr, count, first in where:
        got = F.words_from(seen, first, count)
        want = [0 if a + i in CHIP_CFG else w for a, ws in blocks for i, w in enumerate(ws) if addr <= a + i < addr + count]
        assert got == want, "readback at 0x%04X: %s != %s" % (addr, [hex(x) for x in got], [hex(x) for x in want])


async def setup(dut, seed=1, hold=4):
    p = Pins(dut, hold=hold, seed=seed)
    await p.start()
    await p.enter_load()
    return p


@cocotb.test()
async def test_01_random_images_load(dut):
    """Random images with random strobe spacing load, commit and read back exactly."""
    p = await setup(dut, seed=11, hold=(4, 12))
    rng = random.Random(1)
    for _ in range(3):
        blocks = image(rng)
        await begin(p)
        body, crc = F.load_body(blocks)
        await p.exchange(body + F.commit(crc))
        assert await p.stat() & (LOADED | HOST_ERR) == LOADED
        assert (await p.read(regs.host_addr("LOAD_CRC")))[0] == crc
        await check_readback(p, blocks)


@cocotb.test()
async def test_02_bad_crc(dut):
    """COMMIT with a wrong CRC leaves loaded clear; LOAD_CRC shows the right one."""
    p = await setup(dut)
    blocks = image(random.Random(2))
    await begin(p)
    body, crc = F.load_body(blocks)
    await p.exchange(body + F.commit(crc ^ 0x00010000))
    assert await p.stat() & LOADED == 0
    assert (await p.read(regs.host_addr("LOAD_CRC")))[0] == crc
    await p.exchange(F.commit(crc))                      # the right CRC still commits
    assert await p.stat() & LOADED


@cocotb.test()
async def test_03_corrupted_byte(dut):
    """One data byte changed on the way: COMMIT with the host's CRC is refused, and a
    second COMMIT after loading clears loaded again."""
    p = await setup(dut)
    blocks = [(0x0010, [0x11223344, 0x55667788, 0x99AABBCC])]
    await begin(p)
    body, crc = F.load_body(blocks)
    await p.exchange(body + F.commit(crc))
    assert await p.stat() & LOADED
    bad = list(body)
    bad[9] = (bad[9][0] ^ 0x40, 0)
    await begin(p)
    await p.exchange(bad + F.commit(crc))
    assert await p.stat() & LOADED == 0


@cocotb.test()
async def test_04_truncated_frame(dut):
    """A WRB cut in the middle of a word by the next command byte: whole words before the
    cut are written, the partial word is not, the next command works, and COMMIT with the
    CRC of the intended stream fails. A fresh BEGIN and the full stream load."""
    p = await setup(dut)
    await begin(p)
    await p.exchange(F.wrb(0x0020, [0xAAAAAAAA] * 4))
    old = await p.read(0x0020, 4)
    words = [0x01020304, 0x05060708, 0x090A0B0C, 0x0D0E0F10]
    body, crc = F.load_body([(0x0020, words)])
    await begin(p)
    await p.exchange(body[:5 + 4 * 2 + 2] + F.commit(crc))   # command, address, count, 2.5 words
    assert await p.stat() & LOADED == 0
    assert await p.read(0x0020, 4) == words[:2] + old[2:]
    await begin(p)
    await p.exchange(body + F.commit(crc))
    assert await p.stat() & LOADED
    assert await p.read(0x0020, 4) == words


@cocotb.test()
async def test_05_spurious_strobes(dut):
    """Bare strobes outside a command and unknown commands do nothing (no write, CRC
    unchanged); a duplicated strobe inside a burst shifts the data and COMMIT refuses."""
    p = await setup(dut)
    await begin(p)
    await p.exchange([F.BARE] * 5 + [(0x55, 1), (0x12, 0), (0x34, 0)] + [(0x7F, 0)] * 3)
    assert (await p.read(regs.host_addr("LOAD_CRC")))[0] == 0x00000000       # nothing covered yet
    words = [0xCAFEF00D, 0x12345678]
    body, crc = F.load_body([(0x0030, words)])
    await p.exchange(body + F.commit(crc))
    assert await p.stat() & LOADED
    await begin(p)
    dup = body[:8] + [body[7]] + body[8:]
    await p.exchange(dup + F.commit(crc))
    assert await p.stat() & LOADED == 0


@cocotb.test()
async def test_06_loaded_cleared_on_entry(dut):
    """Leaving and re-entering load mode clears loaded; the first strobe 4 clocks after
    rst_n falls (the host library's guard) is accepted."""
    p = await setup(dut)
    await begin(p)
    body, crc = F.load_body([(0x0000, [1, 2, 3])])
    await p.exchange(body + F.commit(crc))
    assert await p.stat() & LOADED
    await p.leave_load()
    await p.tick(50)
    await p.enter_load(settle=4)
    assert await p.stat() & LOADED == 0


@cocotb.test()
async def test_07_bist(dut):
    """BIST runs, reports pass in STAT and BIST, and SRAM writes during it are refused."""
    p = await setup(dut)
    await begin(p)
    await p.exchange(F.bist())
    await p.exchange(F.wr(0x0005, 0x12345678))
    st = await p.stat()
    assert st & HOST_ERR and not st & BIST_DONE
    await p.tick(12000)
    st = await p.stat()
    assert st & (BIST_DONE | BIST_PASS) == BIST_DONE | BIST_PASS
    assert (await p.read(regs.host_addr("BIST")))[0] == 0
    await begin(p)                                         # BEGIN clears the host error
    assert await p.stat() & HOST_ERR == 0


@cocotb.test()
async def test_08_run_refused_before_commit(dut):
    """Without COMMIT a RUN write over SPI is refused: RUN stays 0, host error set; a RUN
    write in load mode is ignored without an error."""
    p = await setup(dut)
    await begin(p)
    await p.exchange(F.wr(regs.host_addr("RUN"), 1))
    assert await p.stat() & HOST_ERR == 0
    await p.exchange(F.wrb(0x0000, [0, 0, 0, 0]))
    await p.leave_load()
    await p.spi_write(regs.host_addr("RUN"), 1)
    assert (await p.spi_read(regs.host_addr("RUN")))[0] == 0
    st = (await p.spi_read(regs.host_addr("STATUS")))[0]
    assert st & (LOADED | HOST_ERR) == HOST_ERR
