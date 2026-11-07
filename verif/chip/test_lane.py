# SPDX-License-Identifier: Apache-2.0
"""cocotb: a compiled program on a lane in run mode, driving a protocol on the
pins, judged from the pins alone. The same steps as the UART end-to-end demo
(examples/uart/run.sh) on the cocotb bench, so they also run on the routed
netlist with SDF (verif/glsdf): tsec builds specs/uart.tse, the image goes in
over the loader pins, RUN starts both lanes, the host feeds the transmit
mailbox and reads the receive mailbox over run-mode SPI while a scheduled
8N1 line drives the receive pin, and verif.judges decodes a pins-only VCD
that this module writes.

The second test does the same for an SPI controller lane (as in
examples/spi/run.sh controller) against the stand-in flash of
examples/spi_bus.py, which answers MISO on every SCK edge the lane drives.

Every receive line edge lands at a random point inside the clock period, so
on the netlist the input pads switch near either clock edge.
"""
import os
import random
import subprocess
import sys

import cocotb
from cocotb.triggers import First, Timer
from cocotb.utils import get_sim_time

from tse_host import frames as F
from tools.tseasm import regs
from chip_pins import Pins

TOP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(TOP, "examples"))
import spi_bus as sb  # noqa: E402
import tse_demo as td  # noqa: E402

OUT = os.environ.get("LANE_OUT", os.path.join(TOP, "build", "verif-chip", "lane"))
LOADED, HOST_ERR = 0x01, 0x08
PERIOD_PS = 16666
SPI_HALF = 4                    # host SCK at f_clk / 8, the fastest the host SPI takes
UART_BAUD = 2000000             # 30 clocks per bit: short enough for the SDF netlist
UART_TX = b"T\x00\xff\n"
UART_RX = b"U\xa5\x00\xff"
RX_SKEW = (0.0, 1.5, -1.5, 0.0)  # percent, one per receive byte
TX_PAD, RX_PIN = 0, 2          # uo_out[0], uio_in[2]
CTL_PAD = {"cs_n": 4, "sck": 5, "mosi": 6}   # uio[4..6]
MISO_PIN = 7                    # ui_in[7]


class PinTrace:
    """Pins-only VCD of the bench ports, written from value changes."""
    PORTS = ("ui_in", "uio_in", "uo_out", "uio_out", "uio_oe")

    def __init__(self, dut, path):
        self.dut = dut
        self.fh = open(path, "w")
        self.fh.write("$timescale 1ps $end\n$scope module tse_chip $end\n")
        for k, name in enumerate(self.PORTS):
            self.fh.write("$var wire 8 %s %s [7:0] $end\n" % (chr(33 + k), name))
        self.fh.write("$upscope $end\n$enddefinitions $end\n")
        self.last = {}
        self.on = True
        self.dump()
        cocotb.start_soon(self.watch())

    def dump(self):
        now = get_sim_time("ps")
        lines = []
        for k, name in enumerate(self.PORTS):
            v = str(getattr(self.dut, name).value).lower()
            if self.last.get(name) != v:
                self.last[name] = v
                lines.append("b%s %s\n" % (v, chr(33 + k)))
        if lines:
            self.fh.write("#%d\n%s" % (now, "".join(lines)))

    async def watch(self):
        changes = [getattr(self.dut, n).value_change for n in self.PORTS]
        while self.on:
            await First(*changes)
            if self.on:
                self.dump()

    def close(self):
        self.on = False
        self.dump()
        self.fh.write("#%d\n" % get_sim_time("ps"))
        self.fh.close()


def tsec(spec, name, *opts):
    out = os.path.join(OUT, name)
    os.makedirs(out, exist_ok=True)
    cmd = [sys.executable, "-m", "tools.tsec", "build", spec, "--fclk", "60e6",
           "--emit", "img,lst,report,json", "-o", out] + list(opts)
    r = subprocess.run(cmd, cwd=TOP, capture_output=True, text=True)
    with open(os.path.join(out, "tsec.log"), "w") as fh:
        fh.write(r.stdout + r.stderr)
    assert r.returncode == 0 and "RESULT: all roles accepted" in r.stdout, "tsec %s failed" % spec
    return td.read_tsec(out, os.path.splitext(os.path.basename(spec))[0])


def judge(dut, protocol, vcd, tag, t_from, expect, *args):
    """Run the independent judge on the trace; expect is the list of frame keys."""
    exp = "%s.%s.expect" % (vcd[:-4], tag)
    with open(exp, "w") as fh:
        fh.write("".join(k + "\n" for k in expect))
    cmd = [sys.executable, "-m", "verif.judges", protocol, vcd, "--from", "%.12f" % t_from,
           "--quiet", "--expect", exp] + list(args)
    r = subprocess.run(cmd, cwd=TOP, capture_output=True, text=True)
    with open("%s.%s.judge" % (vcd[:-4], tag), "w") as fh:
        fh.write(r.stdout + r.stderr)
    verdict = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
    dut._log.info("judge %s %s: %s", protocol, " ".join(args), verdict)
    assert r.returncode == 0 and verdict.startswith("PASS"), verdict


async def load_and_run(p, blocks, mask, uio_idle=0):
    """Load the image over the byte bus, commit, run mode, RUN = mask."""
    await p.start()
    await p.enter_load()
    stream, crc = F.load_stream(blocks)
    await p.exchange(stream)
    assert await p.stat() & (LOADED | HOST_ERR) == LOADED
    await p.leave_load()
    p.set(uio=uio_idle)
    await p.tick(4)
    await p.spi_write(regs.host_addr("RUN"), mask)
    st = (await p.spi_read(regs.host_addr("STATUS")))[0]
    assert (st >> 8) & mask == mask and not st & HOST_ERR, "STATUS 0x%08X" % st
    return p


def bitrev8(v):
    return int("{:08b}".format(v & 0xFF)[::-1], 2)


async def uart_line(dut, frames, rng):
    """Drive uio_in[RX_PIN] with 8N1 frames: (start_ps, byte, baud). Edges fall at
    exact bit times plus a random offset of up to one clock period."""
    for start, byte, baud in frames:
        bit = 1e12 / baud
        for i, v in enumerate([0] + [(byte >> k) & 1 for k in range(8)] + [1]):
            t = int(start + i * bit) + rng.randrange(PERIOD_PS)
            now = get_sim_time("ps")
            if t > now:
                await Timer(t - now, unit="ps")
            u = int(dut.uio_in.value)
            dut.uio_in.value = u & ~(1 << RX_PIN) | v << RX_PIN


@cocotb.test()
async def test_01_uart(dut):
    """specs/uart.tse at 2 Mbaud: lane tx_side sends UART_TX on uo_out[0] as the host
    feeds its mailbox; lane rx_side receives UART_RX on uio_in[2] at nominal and
    +-1.5 % baud. Both lines pass the UART judge; the host reads the bytes back."""
    lanes = tsec("specs/uart.tse", "uart", "-D", "baud=%d" % UART_BAUD)
    role = {r: lane for lane, (r, _, _, _) in lanes.items()}
    tx, rx = role["tx_side"], role["rx_side"]
    blocks = td.build_blocks(lanes, {"tx": TX_PAD}, {"rx": (td.UIO, RX_PIN)})
    vcd = os.path.join(OUT, "uart.vcd")
    trace = PinTrace(dut, vcd)
    mask = 1 << tx | 1 << rx
    p = await load_and_run(Pins(dut, spi_half=SPI_HALF), blocks, mask, uio_idle=1 << RX_PIN)
    t_run = get_sim_time("ps")

    mbox_tx, mbox_rx = td.chip_reg("MBOX_TX", tx), td.chip_reg("MBOX_RX", rx)
    mbox_st = td.chip_reg("MBOX_STATUS")
    tx_full, rx_full = 1 << 2 * tx, 1 << 2 * rx + 1
    # one receive frame every 2000 clocks: the host reads each before the next ends
    gap = 2000 * PERIOD_PS
    t0 = t_run + 600 * PERIOD_PS
    frames = [(t0 + k * gap, b, UART_BAUD * (1 + s / 100.0)) for k, (b, s) in enumerate(zip(UART_RX, RX_SKEW))]
    line = cocotb.start_soon(uart_line(dut, frames, random.Random(7)))

    queue, got = list(UART_TX), bytearray()
    limit = frames[-1][0] + 4 * gap
    while (queue or len(got) < len(UART_RX)) and get_sim_time("ps") < limit:
        s = (await p.spi_read(mbox_st))[0]
        if s & rx_full:
            got.append(bitrev8((await p.spi_read(mbox_rx))[0]))
        if queue and not s & tx_full:
            await p.spi_write(mbox_tx, queue.pop(0))
    await line
    while (await p.spi_read(mbox_st))[0] & tx_full:
        pass
    await p.tick(12 * 30)
    irq = (await p.spi_read(td.chip_reg("IRQ")))[0]
    st = (await p.spi_read(td.chip_reg("STATUS")))[0]
    trace.close()

    assert bytes(got) == UART_RX, "rx read back %s, want %s" % (got.hex(), UART_RX.hex())
    assert not queue, "%d tx bytes never taken" % len(queue)
    assert irq == 0, "IRQ 0x%04X" % irq
    assert (st >> 8) & mask == mask and not st & HOST_ERR, "STATUS 0x%08X" % st
    t_from = t_run * 1e-12
    judge(dut, "uart", vcd, "tx", t_from, ["%02X" % b for b in UART_TX], "--map", "rx=uo_out[%d]" % TX_PAD,
          "--baud", str(UART_BAUD))
    judge(dut, "uart", vcd, "rx", t_from, ["%02X" % b for b in UART_RX], "--map", "rx=uio_in[%d]" % RX_PIN,
          "--baud", str(UART_BAUD))


class FlashPins(Pins):
    """Pins whose ui_in[MISO] belongs to the stand-in flash in run mode (in load
    mode ui_in is the byte bus); the stand-in sets it through own/_push, as it
    would on DrivenPins."""

    def __init__(self, dut):
        Pins.__init__(self, dut, spi_half=SPI_HALF)
        self.own = {td.UI: [0] * 8, td.UIO: [0] * 8}
        self.ui = 0
        self.run = False

    def set(self, ui=None, uio=None, rst=None):
        if ui is not None and self.run:
            self.ui = ui
            ui = ui & ~(1 << MISO_PIN) | self.own[td.UI][MISO_PIN] << MISO_PIN
        Pins.set(self, ui, uio, rst)

    def _push(self):
        if self.run:
            self.set(ui=self.ui)

    async def leave_load(self):
        self.run = True
        await Pins.leave_load(self)


async def flash_watch(dut, p, flash):
    """Wake the stand-in flash on every change of the controller pads."""
    while True:
        await First(dut.uio_out.value_change, dut.uio_oe.value_change)
        out, oe = dut.uio_out.value, dut.uio_oe.value
        if out.is_resolvable and oe.is_resolvable:
            flash.update(p, int(out), int(oe))


@cocotb.test()
async def test_02_spi_controller(dut):
    """specs/spi_controller_mode0.tse (zero pad skew): the lane drives CS#, SCK and MOSI
    on uio[4..6] and reads MISO on ui_in[7] from a stand-in serial NOR flash. The
    host steps the lane over its mailbox: JEDEC ID 9F with three dummy bytes, read
    status 05 with one. The SPI judge decodes both chip-select frames."""
    lanes = tsec("specs/spi_controller_mode0.tse", "spi_ctl", "--pad-skew", "0")
    pads = {w: 8 + b for w, b in CTL_PAD.items()}
    blocks = td.build_blocks(lanes, pads, {"miso": (td.UI, MISO_PIN)})
    vcd = os.path.join(OUT, "spi_ctl.vcd")
    trace = PinTrace(dut, vcd)
    p = FlashPins(dut)
    flash = sb.SpiTargetStandIn(CTL_PAD["cs_n"], CTL_PAD["sck"], CTL_PAD["mosi"], (td.UI, MISO_PIN),
                                sb.flash_answer(0xEF4015, bytes(range(256))))
    cocotb.start_soon(flash_watch(dut, p, flash))
    await load_and_run(p, blocks, 1)
    t_run = get_sim_time("ps")

    # One SPI transaction of the host takes about 450 clocks, a lane step under 150,
    # so each step word is pulled, and each byte pushed, before the next transaction.
    mb = sb.Mailbox(None, 0)
    reads = []
    for out, nread in ((b"\x9f", 3), (b"\x05", 1)):
        await p.spi_write(mb.tx, 1)
        got = []
        for b in list(out) + [0] * nread:
            await p.spi_write(mb.tx, 2)
            await p.spi_write(mb.tx, sb.bitrev(b, 8))
            got.append((await p.spi_read(mb.rx))[0] & 0xFF)
        await p.spi_write(mb.tx, 3)
        reads.append(bytes(got[len(out):]))
    await p.tick(200)
    irq = (await p.spi_read(td.chip_reg("IRQ")))[0]
    st = (await p.spi_read(td.chip_reg("STATUS")))[0]
    trace.close()

    assert reads == [bytes.fromhex("ef4015"), b"\x00"], "host read %s" % [r.hex() for r in reads]
    assert [f[0] for f in flash.frames] == [bytes.fromhex("9f000000"), bytes.fromhex("0500")], \
        "flash saw %s" % [f[0].hex() for f in flash.frames]
    assert irq == 0, "IRQ 0x%04X" % irq
    assert (st >> 8) & 1 and not st & HOST_ERR, "STATUS 0x%08X" % st
    judge(dut, "spi", vcd, "spi", t_run * 1e-12, ["mosi=9F,00,00,00 miso=00,EF,40,15", "mosi=05,00 miso=00,00"],
          "--map", "cs=uio_out[4]", "--map", "clk=uio_out[5]", "--map", "mosi=uio_out[6]",
          "--map", "miso=ui_in[%d]" % MISO_PIN, "--mode", "0")
