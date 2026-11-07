"""CAN receiver lane against the simulator's bit-level CAN node, fed with
struct can_frame records as a raw SocketCAN socket carries them.

The lane runs can_rx_ack with txd on the uio[7] pad and rxd on uio[6]
(ui_in is the loader's byte bus, and the simulator's wired-AND bus holds
its input pin at the bus level all the time). tse_sim --can unix:PATH puts
a remote CAN node on that bus: it builds every frame it gets on the socket
(CRC-15, stuffing), sends it at --can-bit clocks per bit, samples the ACK
slot and logs 'tx ID acked' only when another node, here the chip, drove
it dominant. With vcan in the kernel the same node runs as --can vcan0 and
the frames come from cansend; this kernel has no vcan module.

Each frame must be acknowledged on the bus and pushed by the lane as three
host words that decode to the frame that was sent.
"""
import argparse
import os
import socket
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import can_demo as cd  # noqa: E402
import tse_demo as td  # noqa: E402

TXD, RXD = 7, 6                     # uio bits
POLL = 200


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--tsec", nargs=2, required=True, metavar=("DIR", "NAME"))
    ap.add_argument("--bitrate", type=float, default=500e3)
    a = ap.parse_args()
    bit = int(td.FCLK / a.bitrate)

    lanes = td.read_tsec(*a.tsec)
    blocks = td.build_blocks(lanes, {"txd": 8 + TXD}, {"rxd": (td.UIO, RXD)})
    chip = td.Chip(a.sim, log=True)
    path = chip.pipe.wait_line("can A ").split()[2]
    crc = chip.load(blocks)
    st = td.status_fields(chip.start(1))
    chip.pipe.vcd_start()
    run_at = chip.pins.cycle
    print("loaded: crc 0x%08X; run: lane 0 from cycle %d, bit %d cycles, STATUS %s"
          % (crc, run_at, bit, st), flush=True)

    sk = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sk.connect(path)
    st_reg = td.chip_reg("MBOX_STATUS")
    rx_reg, rx_full = td.chip_reg("MBOX_RX", 0), 1 << 1
    bad = []
    for n, (ident, data) in enumerate(cd.FRAMES):
        sk.sendall(struct.pack("<IB3x8s", ident, len(data), data))
        want = "%03X#%s" % (ident, data.hex().upper())
        got = []
        limit = chip.pins.cycle + 400 * bit
        while len(got) < 3 and chip.pins.cycle < limit:
            if chip.peek(st_reg) & rx_full:
                got.append(chip.peek(rx_reg))
            else:
                chip.wait(POLL)
        chip.wait(20 * bit)
        acked = [x for x in chip.log() if x.startswith("can A: tx %03X acked" % ident)]
        if len(got) != 3:
            bad.append("frame %d: %d words pushed" % (n, len(got)))
            continue
        ident2, _, data2 = cd.decode(got)
        key = "%03X#%s" % (ident2, data2.hex().upper())
        print("frame %d %s: %s, host %s (words %s)" % (n, want, acked[-1][7:] if acked else "not acked",
              key, " ".join("%08X" % w for w in got)), flush=True)
        if len(acked) != 1:
            bad.append("frame %d acknowledged %d times on the bus" % (n, len(acked)))
        if key != want:
            bad.append("frame %d: host got %s" % (n, key))
    sk.close()
    chip.wait(20 * bit)
    st = td.status_fields(chip.peek(td.chip_reg("STATUS")))
    end = chip.pins.cycle
    chip.close()
    for ln in chip.log():
        if ln.startswith("can A: tx "):
            print(ln)
    print("end: cycle %d (%.3f ms of chip time after RUN), STATUS %s"
          % (end, (end - run_at) * 1e3 / td.FCLK, st))
    if st["host_err"] or st["halted"]:
        bad.append("host error or halted lane")
    if bad:
        print("FAIL host: " + "; ".join(bad))
        return 1
    print("PASS host: %d can_frame records sent by the CAN node, each acknowledged by the lane "
          "in its ACK slot and pushed" % len(cd.FRAMES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
