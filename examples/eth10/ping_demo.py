# SPDX-License-Identifier: Apache-2.0
"""The Linux kernel answers frames sent by the 10BASE-T transmit lane.

Runs inside a user and network namespace (run_ping.sh) where tap0 is the
kernel's interface, 10.0.0.1 with MAC 02:00:00:00:00:01. tse_sim --tap
tap0 plays the remote PHY: it decodes the lane's td pad (uo_out[1]),
checks the FCS and hands good frames to the kernel; frames the kernel
sends are Manchester coded onto uio[5], the chip's receive pin. Nothing
reads that pin: the receive spec (specs/eth10_rx.tse) is rejected (R314).

The lane runs eth10_tx_window as in eth_demo.py. The host sends the frames
of ping.expect from 10.0.0.2 (02:00:00:00:00:02): an ARP request for
10.0.0.1, then an ICMP echo request. The kernel learns the chip's address
from the request, so it answers the echo without asking again. The pcap
of tap0, written by the simulator with model-time stamps, must hold both
frames as sent and the kernel's ARP reply and echo reply.
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import eth_demo as ed  # noqa: E402
import spi_bus as sb  # noqa: E402
import tse_demo as td  # noqa: E402

CHIP_MAC = bytes.fromhex("020000000002")
REPLY_CYCLES = 300000                   # 5 ms of chip time for the kernel to answer


def read_pcap(path):
    """[(us, frame)] of a pcap file."""
    with open(path, "rb") as fh:
        data = fh.read()
    out, i = [], 24
    while i + 16 <= len(data):
        sec, usec, n, _ = struct.unpack_from("<IIII", data, i)
        out.append((sec * 1000000 + usec, data[i + 16:i + 16 + n]))
        i += 16 + n
    return out


def kind(fr):
    """A short name of a frame, or None."""
    et = fr[12:14]
    if et == b"\x08\x06":
        op = fr[20:22]
        return "ARP %s %s" % ("request" if op == b"\0\1" else "reply",
                              ".".join(str(x) for x in fr[38:42]))
    if et == b"\x08\x00" and fr[23] == 1:
        t = fr[34]
        ident, seq = struct.unpack_from("!HH", fr, 38)
        return "ICMP %s id 0x%04X seq %d data %s" % ({8: "echo request", 0: "echo reply"}.get(t, t),
                                                    ident, seq, fr[42:14 + 46].hex())
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--tsec", nargs=2, required=True, metavar=("DIR", "NAME"))
    ap.add_argument("--vcd", required=True)
    ap.add_argument("--line", required=True)
    ap.add_argument("--pcap", required=True)
    ap.add_argument("--frames", required=True)
    a = ap.parse_args()
    keys = [ln.strip() for ln in open(a.frames) if ln.strip() and not ln.startswith("#")]
    lanes = td.read_tsec(*a.tsec)
    chip = td.Chip(a.sim, log=True)
    chip.pipe.adapters(False)           # the PHY must not see the loader's pins
    crc = chip.load(td.build_blocks(lanes, ed.PADS, {}))
    st = td.status_fields(chip.start(1))
    chip.pipe.adapters(True)
    chip.pipe.vcd_start()
    run_at = chip.pins.cycle
    print("loaded: crc 0x%08X; run: lane 0 from cycle %d, STATUS %s" % (crc, run_at, st))
    mb = sb.Mailbox(chip, 0)
    for key in keys:
        body = ed.frame_bytes(key)
        n = ed.stage(chip, mb, body, 0)
        print("frame: %s, %d octets, header pulled at cycle %d" % (kind(body), len(body), chip.pins.cycle))
        chip.wait((n + 4) * ed.WORD_CYCLES + 1000)
        for _ in range(REPLY_CYCLES // 20000):
            chip.wait(20000)
    irq = chip.peek(td.chip_reg("IRQ"))
    st = td.status_fields(chip.peek(td.chip_reg("STATUS")))
    end = chip.pins.cycle
    chip.close()
    for ln in chip.log():
        if ln.startswith("eth"):
            print(ln)
    print("end: cycle %d (%.3f ms of chip time after RUN), IRQ 0x%04X, STATUS %s"
          % (end, (end - run_at) * 1e3 / td.FCLK, irq, st))
    print("line: %d changes written to %s" % (ed.line_vcd(a.vcd, a.line), a.line))

    sent = [ed.frame_bytes(k) for k in keys]
    to_tap, from_tap = [], []
    for us, fr in read_pcap(a.pcap):
        (to_tap if fr[6:12] == CHIP_MAC else from_tap).append(fr)
        print("pcap %8.3f ms  %s  %s -> %s  %s" % (us / 1e3, "chip -> kernel" if fr[6:12] == CHIP_MAC
              else "kernel -> chip", fr[6:12].hex(), fr[0:6].hex(), kind(fr) or "type " + fr[12:14].hex()))
    names = [kind(fr) for fr in from_tap]
    bad = []
    if [fr[:len(s)] for fr, s in zip(to_tap, sent)] != sent:
        bad.append("the kernel got %d frames, not the %d sent" % (len(to_tap), len(sent)))
    if "ARP reply 10.0.0.2" not in names:
        bad.append("no ARP reply from the kernel")
    echo = kind(sent[1]).replace("echo request", "echo reply")
    if echo not in names:
        bad.append("no echo reply from the kernel")
    if irq:
        bad.append("interrupt bits 0x%04X" % irq)
    if st["host_err"] or st["halted"] or st["running"] != 1:
        bad.append("host error, halted or stopped lane")
    if bad:
        print("FAIL host: " + "; ".join(bad))
        return 1
    print("PASS host: the kernel took both frames and answered: ARP reply, %s" % echo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
