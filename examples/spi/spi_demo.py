"""SPI target and SPI controller lanes on the Verilated chip, mode 0.

--role target: the lane runs spi_target_mode0_css114 on ui_in[4] (CS#),
ui_in[5] (SCK), ui_in[6] (MOSI) and drives MISO on uo_out[1]. The host
loads 12 34 as the answer, a stand-in controller on the input pins sends
A5 5A in one chip-select frame (CS falls one SCK period before the first
rise, more than the 114 ns the spec asks), and the host reads A5 and 5A
from the mailbox.

--role controller: the lane runs spi_controller_mode0 with its pads on
uio[4] (CS#), uio[5] (SCK), uio[6] (MOSI) and reads MISO from ui_in[7],
where a stand-in serial NOR flash answers. The host sends the JEDEC ID
command 9F with three dummy bytes and read status 05 with one, and checks
the ID EF 40 15 and status 00.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import spi_bus as sb  # noqa: E402
import tse_demo as td  # noqa: E402

TGT_IN = {"cs_n": (td.UI, 4), "sck": (td.UI, 5), "mosi": (td.UI, 6)}
TGT_MISO = 1
CTL_PAD = {"cs_n": 4, "sck": 5, "mosi": 6}
CTL_MISO = (td.UI, 7)
JEDEC = 0xEF4015


def run_target(chip, lanes, half):
    blocks = td.build_blocks(lanes, {"miso": TGT_MISO}, TGT_IN)
    crc = chip.load(blocks)
    st = td.status_fields(chip.start(1))
    stand = sb.SpiStandIn(chip.pins, TGT_IN["cs_n"], TGT_IN["sck"], TGT_IN["mosi"],
                          (sb.UO, TGT_MISO), half)
    stand.idle()
    chip.pipe.vcd_start()
    print("loaded: crc 0x%08X; run: lane 0, STATUS %s" % (crc, st))
    mb = sb.Mailbox(chip, 0)
    chip.poke(mb.tx, sb.bitrev(0x12340000, 32))
    while mb.status() & mb.tx_full:
        chip.wait(50)
    chip.wait(2 * half)
    got = []

    def take(k):
        while not mb.status() & mb.rx_full:
            chip.wait(20)
        got.append(chip.peek(mb.rx) & 0xFF)

    _, miso = stand.xfer(b"\xa5\x5a", 0, between=take)
    print("frame: mosi=a55a miso=%s; host read %s" % (miso.hex(), bytes(got).hex()))
    bad = []
    if miso != b"\x12\x34":
        bad.append("MISO %s, the host loaded 1234" % miso.hex())
    if bytes(got) != b"\xa5\x5a":
        bad.append("lane pushed %s" % bytes(got).hex())
    return bad, 1


def run_controller(chip, lanes):
    pads = {w: 8 + b for w, b in CTL_PAD.items()}
    blocks = td.build_blocks(lanes, pads, {"miso": CTL_MISO})
    crc = chip.load(blocks)
    st = td.status_fields(chip.start(1))
    flash = sb.SpiTargetStandIn(CTL_PAD["cs_n"], CTL_PAD["sck"], CTL_PAD["mosi"], CTL_MISO,
                                sb.flash_answer(JEDEC, bytes(range(256))))
    chip.pins.hold(CTL_MISO[0], CTL_MISO[1], 0)
    chip.pins.attach(flash)
    chip.pipe.vcd_start()
    print("loaded: crc 0x%08X; run: lane 0, STATUS %s" % (crc, st))
    ch = sb.ControllerHost(chip, 0)
    ident, _ = ch.xfer(b"\x9f", 3)
    sr, _ = ch.xfer(b"\x05", 1)
    for mosi, miso in flash.frames:
        print("frame: mosi=%s miso=%s" % (mosi.hex(), miso.hex()))
    print("host: JEDEC ID %s, status %s" % (ident.hex(), sr.hex()))
    bad = []
    if ident != bytes.fromhex("ef4015"):
        bad.append("JEDEC ID %s" % ident.hex())
    if sr != b"\x00":
        bad.append("status %s" % sr.hex())
    if len(flash.frames) != 2:
        bad.append("%d frames seen by the flash" % len(flash.frames))
    return bad, 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--tsec", nargs=2, required=True, metavar=("DIR", "NAME"))
    ap.add_argument("--role", choices=("target", "controller"), required=True)
    ap.add_argument("--half", type=int, default=8, help="stand-in controller: clocks per SCK half")
    a = ap.parse_args()
    lanes = td.read_tsec(*a.tsec)
    chip = td.Chip(a.sim)
    if a.role == "target":
        bad, mask = run_target(chip, lanes, a.half)
    else:
        bad, mask = run_controller(chip, lanes)
    chip.wait(200)
    irq = chip.peek(td.chip_reg("IRQ"))
    st = td.status_fields(chip.peek(td.chip_reg("STATUS")))
    print("end: cycle %d, IRQ 0x%04X, STATUS %s" % (chip.pins.cycle, irq, st))
    chip.close()
    if irq:
        bad.append("interrupt bits 0x%04X" % irq)
    if st["host_err"] or st["halted"] or st["running"] != mask:
        bad.append("host error, halted or stopped lane")
    if bad:
        print("FAIL host: " + "; ".join(bad))
        return 1
    print("PASS host: SPI %s lane, every byte as expected" % a.role)
    return 0


if __name__ == "__main__":
    sys.exit(main())
