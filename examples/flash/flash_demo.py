"""A SPI NOR flash compiled from spi_flash_target.tse, read over serprog.

The flash lane listens on ui_in[4] (CS#), ui_in[5] (SCK) and ui_in[6]
(MOSI) and drives MISO on uo_out[1]. This script loads the image, starts
the lane, starts the VCD, then serves flashrom's serprog protocol on a TCP
port. Each SPI operation from the client runs on the pins:

  stand-in   a mode 0 controller made of the input pins (the testbench
             part of a board), at the rate the client asks for, at most
             the 500 kHz the flash lane meets (the spec says why);
  --lanes    lane 0 runs a compiled SPI controller (spi_controller_mode0)
             whose pads drive uio[4..6]; the flash lane reads those pads and
             drives MISO on uio[7], which the controller reads back. Both
             sides of the bus are lanes.

The flash lane's host keeps the image (byte a holds a mod 256), the write
enable latch and the status, as the spec says. It prints 'serprog
127.0.0.1:PORT', serves one client and checks that every opcode, address
and program byte the lane pushed matches what the controller sent.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import spi_bus as sb  # noqa: E402
import tse_demo as td  # noqa: E402

FLASH_IN = {"cs_n": (td.UI, 4), "sck": (td.UI, 5), "mosi": (td.UI, 6)}
FLASH_MISO = 1                          # uo_out[1]
BUS = {"cs_n": 4, "sck": 5, "mosi": 6, "miso": 7}     # uio pads with --lanes
MAX_HZ = 500000                         # the flash lane's SCK limit
JEDEC = 0xEF4015
SIZE = 1 << 21


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--flash", nargs=2, required=True, metavar=("DIR", "NAME"))
    ap.add_argument("--ctl", nargs=2, metavar=("DIR", "NAME"), help="controller lane build (--lanes)")
    ap.add_argument("--ctl-hz", type=float, default=5e5, help="SCK the controller lane was built for")
    a = ap.parse_args()
    image = bytearray(i & 0xFF for i in range(SIZE))

    flash = td.read_tsec(*a.flash)
    if a.ctl:
        ctl = td.read_tsec(*a.ctl)
        lanes = dict(ctl)
        lanes.update(td.move_lane(flash, 0, 1))
        pads = {"cs_n": 8 + BUS["cs_n"], "sck": 8 + BUS["sck"], "mosi": 8 + BUS["mosi"],
                "miso": 8 + BUS["miso"]}
        inputs = {"miso": (td.UIO, BUS["miso"])}
        inputs.update({w: (td.UIO, BUS[w]) for w in ("cs_n", "sck", "mosi")})
        fl, cl = 1, 0
    else:
        lanes = flash
        pads = {"miso": FLASH_MISO}
        inputs = FLASH_IN
        fl, cl = 0, None
    blocks = td.build_blocks(lanes, pads, inputs)

    chip = td.Chip(a.sim)
    stand = None
    if cl is None:
        stand = sb.SpiStandIn(chip.pins, FLASH_IN["cs_n"], FLASH_IN["sck"], FLASH_IN["mosi"],
                              (sb.UO, FLASH_MISO), 10)
    crc = chip.load(blocks)
    mask = 1 << fl | (1 << cl if cl is not None else 0)
    st = td.status_fields(chip.start(mask))
    if stand:
        stand.idle()               # after the load: in load mode ui_in is the byte bus
    chip.pipe.vcd_start()
    run_at = chip.pins.cycle
    print("loaded: crc 0x%08X; run: lanes %s from cycle %d, STATUS %s"
          % (crc, "flash %d" % fl + ("" if cl is None else ", controller %d" % cl), run_at, st))

    host = sb.FlashHost(chip, fl, JEDEC, image)
    frames = []
    if cl is None:
        def freq(hz):
            stand.half = max(-(-td.FCLK // (2 * min(hz, MAX_HZ))), 1)
            stand.css = stand.csd = 2 * stand.half
            return td.FCLK // (2 * stand.half)

        def spi(out, nread):
            host.stage(out, nread)
            got, both = stand.xfer(out, nread, between=host.settle)
            host.finish()
            frames.append((bytes(out) + bytes(nread), both))
            return got
    else:
        ch = sb.ControllerHost(chip, cl)

        def freq(hz):
            return int(a.ctl_hz)

        def spi(out, nread):
            host.stage(out, nread)
            got, both = ch.xfer(out, nread, between=lambda k: host.service(), wide=True)
            host.finish()
            frames.append((bytes(out) + bytes(nread), both))
            return got

    srv = sb.SerprogServer(spi, freq)
    print("serprog 127.0.0.1:%d" % srv.port, flush=True)
    srv.serve()
    chip.wait(200)
    irq = chip.peek(td.chip_reg("IRQ"))
    st = td.status_fields(chip.peek(td.chip_reg("STATUS")))
    end = chip.pins.cycle
    chip.close()

    for mosi, miso in frames:
        print("frame: mosi=%s miso=%s" % (mosi.hex(), miso.hex()))
    print("end: cycle %d (%.3f ms of chip time after RUN), %d serprog SPI ops, IRQ 0x%04X, STATUS %s"
          % (end, (end - run_at) * 1e3 / td.FCLK, srv.ops, irq, st))
    bad = list(host.bad)
    if irq:
        bad.append("interrupt bits 0x%04X" % irq)
    if st["host_err"] or st["halted"] or st["running"] != mask:
        bad.append("host error, halted or stopped lane")
    if not frames:
        bad.append("no SPI operation")
    if bad:
        print("FAIL host: " + "; ".join(bad))
        return 1
    print("PASS host: %d frames, every opcode, address and data byte pushed as sent" % len(frames))
    return 0


if __name__ == "__main__":
    sys.exit(main())
