"""10BASE-T transmit lane on the Verilated chip.

The lane runs eth10_tx_window (the transmitter of specs/eth10_tx.tse with the
frame buffered in the data window) with td on uo_out[1] and de on uo_out[2].
For every frame key the host writes the frame's words into the data window
over runtime SPI, last word first in word 0, then puts the header word in the
lane's mailbox; the lane sends preamble, SFD, the words and the FCS at
10 Mbit/s, then TP_IDL and the interframe gap.

The judge reads one line level: TD+ as the transformer sees it, td while the
driver is enabled and idle (low) while it is not. The lane leaves td at its
TP_IDL level after the frame and turns the driver off, so the line is written
to a VCD of its own as td AND de.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import spi_bus as sb  # noqa: E402
import tse_demo as td  # noqa: E402
from tse_host import regs_map  # noqa: E402
from verif.judges.vcd import read_vcd  # noqa: E402

PADS = {"td": 1, "de": 2}
DATA = regs_map.REGIONS["data"]["base"]
WORD_CYCLES = 32 * 6                    # one 32-bit word on the line at 60 MHz


def frame_bytes(key):
    """Destination, source, type and payload of a judge frame key, as bytes."""
    return bytes.fromhex("".join(key.split()))


def stage(chip, mb, body, base):
    """Write body into the data window (last word at base) and pull the header; the frame's word
    count, so the caller knows how long to wait."""
    words = [int.from_bytes(body[i:i + 4].ljust(4, b"\0"), "little") for i in range(0, len(body), 4)]
    last_bits = 8 * (len(body) - 4 * (len(words) - 1))
    for j, w in enumerate(words):
        chip.poke(DATA + base + len(words) - 1 - j, w)
    header = (0x20 + last_bits - 1) << 16 | (base + len(words) - 1)
    while mb.status() & mb.tx_full:
        chip.wait(50)
    chip.poke(mb.tx, header)
    return len(words)


def line_vcd(src, dst):
    """td AND de from the pins-only VCD, as one signal named line (1 ns units)."""
    tr = read_vcd(src, {"td": "uo_out%d" % PADS["td"], "de": "uo_out%d" % PADS["de"]})
    tdl, del_ = tr["td"], tr["de"]
    times = sorted(set(tdl.times) | set(del_.times))
    with open(dst, "w") as fh:
        fh.write("$timescale 1 ns $end\n$scope module top $end\n$var wire 1 ! line $end\n"
                 "$upscope $end\n$enddefinitions $end\n")
        last = None
        for t in times:
            v = int(tdl.at(t) == 1 and del_.at(t) == 1)
            if v != last:
                fh.write("#%d\n%d!\n" % (t, v))
                last = v
        fh.write("#%d\n" % tr.end)
    gaps = {}
    for a, b in zip(tdl.times, tdl.times[1:]):
        if del_.at(a) == 1 and b - a < 150:
            gaps[b - a] = gaps.get(b - a, 0) + 1
    print("td: spacing of transitions inside frames, ns: %s" % ", ".join(
        "%d (%d times)" % kv for kv in sorted(gaps.items())))
    return len(times)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--tsec", nargs=2, required=True, metavar=("DIR", "NAME"))
    ap.add_argument("--vcd", required=True, help="the pins-only VCD the simulator writes")
    ap.add_argument("--line", required=True, help="the line VCD written for the judge")
    ap.add_argument("--frames", required=True, help="frame keys, one per line (the judge's expect file)")
    a = ap.parse_args()
    keys = [ln.strip() for ln in open(a.frames) if ln.strip() and not ln.startswith("#")]
    lanes = td.read_tsec(*a.tsec)
    chip = td.Chip(a.sim)
    blocks = td.build_blocks(lanes, PADS, {})
    crc = chip.load(blocks)
    st = td.status_fields(chip.start(1))
    chip.pipe.vcd_start()
    print("loaded: crc 0x%08X; run: lane 0, STATUS %s" % (crc, st))
    mb = sb.Mailbox(chip, 0)
    for key in keys:
        body = frame_bytes(key)
        n = stage(chip, mb, body, 0)
        print("frame: %d octets in %d data window words, header pulled at cycle %d"
              % (len(body), n, chip.pins.cycle))
        chip.wait((n + 4) * WORD_CYCLES + 1000)     # prefix, words, FCS, TP_IDL and the gap
    chip.wait(500)
    irq = chip.peek(td.chip_reg("IRQ"))
    st = td.status_fields(chip.peek(td.chip_reg("STATUS")))
    print("end: cycle %d, IRQ 0x%04X, STATUS %s" % (chip.pins.cycle, irq, st))
    chip.close()
    print("line: %d changes written to %s" % (line_vcd(a.vcd, a.line), a.line))
    bad = []
    if irq:
        bad.append("interrupt bits 0x%04X" % irq)
    if st["host_err"] or st["halted"] or st["running"] != 1:
        bad.append("host error, halted or stopped lane")
    if bad:
        print("FAIL host: " + "; ".join(bad))
        return 1
    print("PASS host: %d frame(s) staged and pulled" % len(keys))
    return 0


if __name__ == "__main__":
    sys.exit(main())
