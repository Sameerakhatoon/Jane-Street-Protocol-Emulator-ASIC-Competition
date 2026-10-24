"""cocotb: the run-mode SPI target of the whole chip: register reads and writes on
the host map, bursts, STAT, the data window alias, and the refusals while a lane
runs (BEGIN, lane configuration, a running lane's program window).
"""
import os

import cocotb

from tse_host import frames as F
from tools.tseasm import regs
from chip_pins import Pins

LANES = int(os.environ.get("LANES", "2"))
LOADED, HOST_ERR = 0x01, 0x08


async def loaded_chip(dut, extra=()):
    """Load a lane 0 program that loops on word 0 (JMP 0), commit, go to run mode."""
    p = Pins(dut)
    await p.start()
    await p.enter_load()
    blocks = [(0x0000, [0, 0, 0, 0]), (regs.host_addr("LANE_PROG", lane=0), [0])] + list(extra)
    stream, crc = F.load_stream(blocks)
    await p.exchange(stream)
    assert await p.stat() & LOADED
    await p.leave_load()
    return p


@cocotb.test()
async def test_01_registers(dut):
    """ID, STATUS, chip configuration reads 0 (write-only), bursts and the data window alias."""
    pad = (regs.host_addr("PAD_OUT", index=5), [0x1C3])
    p = await loaded_chip(dut, [pad])
    ident = (await p.spi_read(regs.host_addr("ID")))[0]
    assert ident & 0xFF == 0x54 and (ident >> 12) & 7 == LANES and (ident >> 16) & 0xF == 4
    assert (await p.spi_read(regs.host_addr("STATUS")))[0] & (LOADED | HOST_ERR) == LOADED
    assert (await p.spi_read(regs.host_addr("PAD_OUT", index=5)))[0] == 0
    assert await p.spi_stat() & LOADED
    await p.spi(bytes([F.WRB]) + F.be16(0x0100) + F.be16(3) + F.words_bytes([7, 8, 9]), 0)
    assert await p.spi_read(0x0100, 3) == [7, 8, 9]
    await p.spi_write(0x0405, 0xDEADBEEF)
    assert (await p.spi_read(0x0185))[0] == 0xDEADBEEF
    assert (await p.spi_read(0x7777))[0] == 0                 # unmapped


@cocotb.test()
async def test_02_refusals_while_running(dut):
    """With lane 0 running: BEGIN, a lane 0 configuration write and a window 0 write are
    refused and set host error; window 1 is writable; after RUN = 0 BEGIN is
    accepted and clears the error."""
    p = await loaded_chip(dut)
    await p.spi_write(regs.host_addr("RUN"), 1)
    await p.tick(20)
    st = (await p.spi_read(regs.host_addr("STATUS")))[0]
    assert (st >> 8) & 1 and not st & HOST_ERR
    poly = regs.host_addr("CRC_POLY", lane=0)
    await p.spi_write(poly, 0x12345678)
    assert (await p.spi_read(poly))[0] == 0
    await p.spi_write(0x0001, 0x55)
    assert (await p.spi_read(0x0001))[0] == 0
    await p.spi_write(0x0081, 0x66)
    assert (await p.spi_read(0x0081))[0] == 0x66
    await p.spi(bytes([F.BEGIN]), 0)
    st = (await p.spi_read(regs.host_addr("STATUS")))[0]
    assert st & (LOADED | HOST_ERR) == LOADED | HOST_ERR
    await p.spi_write(regs.host_addr("RUN"), 0)
    await p.tick(20)
    await p.spi(bytes([F.BEGIN]), 0)
    st = (await p.spi_read(regs.host_addr("STATUS")))[0]
    assert st & (LOADED | HOST_ERR) == 0
