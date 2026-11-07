# SPDX-License-Identifier: Apache-2.0
# Full-chip tests through the Tiny Tapeout pins only, so the same file runs
# on the gate-level netlist. The Makefile picks this module instead of
# test.py when info.yaml names the chip's top. Keep to the cocotb 2.0.1 API:
# the template CI job pins that version. The pin drivers, load streams and
# register map come from the repository (standard library only).
import os
import random
import sys

import cocotb

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path[:0] = [os.path.join(ROOT, "host"), ROOT, os.path.join(ROOT, "verif", "chip")]

from tse_host import frames as F      # noqa: E402
from tools.tseasm import regs         # noqa: E402
from chip_pins import Pins            # noqa: E402

LANES = 2
LOADED, HOST_ERR = 0x01, 0x08


@cocotb.test()
async def test_01_load_commit_readback(dut):
    """A random image loads over the byte bus, commits with the right CRC and reads back."""
    p = Pins(dut, hold=(4, 8), seed=3)
    await p.start()
    await p.enter_load()
    rng = random.Random(5)
    blocks = [(0x0000, [rng.getrandbits(32) for _ in range(16)]),
              (0x0100, [rng.getrandbits(32) for _ in range(8)])]
    await p.exchange(F.begin())
    body, crc = F.load_body(blocks)
    await p.exchange(body + F.commit(crc))
    assert await p.stat() & (LOADED | HOST_ERR) == LOADED
    assert (await p.read(regs.host_addr("LOAD_CRC")))[0] == crc
    for addr, words in blocks:
        assert await p.read(addr, len(words)) == words, "readback at 0x%04X" % addr


@cocotb.test()
async def test_02_run_mode_spi(dut):
    """After a commit the run-mode SPI target answers ID and STATUS and reads and writes the data window."""
    p = Pins(dut)
    await p.start()
    await p.enter_load()
    stream, _ = F.load_stream([(0x0000, [0, 0, 0, 0]), (regs.host_addr("LANE_PROG", lane=0), [0])])
    await p.exchange(stream)
    assert await p.stat() & LOADED
    await p.leave_load()
    ident = (await p.spi_read(regs.host_addr("ID")))[0]
    assert ident & 0xFF == 0x54 and (ident >> 12) & 7 == LANES, "ID 0x%08X" % ident
    assert (await p.spi_read(regs.host_addr("STATUS")))[0] & (LOADED | HOST_ERR) == LOADED
    await p.spi(bytes([F.WRB]) + F.be16(0x0100) + F.be16(3) + F.words_bytes([7, 8, 9]), 0)
    assert await p.spi_read(0x0100, 3) == [7, 8, 9]
