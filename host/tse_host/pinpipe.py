# SPDX-License-Identifier: Apache-2.0
"""A pin bus over a pipe, so a session can drive a chip simulated in another process.

Line protocol, client to server, numbers in hex:
    S <ui_in> <uio_in> <rst_n>    set the input pins (no reply)
    T <n>                         run n clock cycles (no reply)
    R                             reply "<uo_out>\n" as after the last cycle
    Q                             quit
The client buffers S and T lines and sends them with the next R, so a load
costs one round trip per sampled byte. Any simulator that answers these four
lines can stand behind PinLink, for example a Verilated chip in the C++
harness. `python3 -m tse_host.pinpipe` serves the Python chip model.

CPython only (subprocess).
"""
import subprocess
import sys


class PipePins:
    def __init__(self, argv):
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     text=True, bufsize=1)
        self.buf = []
        self.ui_in, self.uio_in, self.rst_n = 0, 0, 1

    def set_inputs(self, ui_in=None, uio_in=None, rst_n=None):
        if ui_in is not None:
            self.ui_in = ui_in & 0xFF
        if uio_in is not None:
            self.uio_in = uio_in & 0xFF
        if rst_n is not None:
            self.rst_n = 1 if rst_n else 0
        self.buf.append("S %x %x %x" % (self.ui_in, self.uio_in, self.rst_n))

    def tick(self, n=1):
        if n:
            self.buf.append("T %x" % n)

    @property
    def uo_out(self):
        self.buf.append("R")
        self.proc.stdin.write("\n".join(self.buf) + "\n")
        self.proc.stdin.flush()
        self.buf = []
        line = self.proc.stdout.readline()
        if not line:
            raise IOError("pin server closed the pipe")
        return int(line, 16)

    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.write("\n".join(self.buf + ["Q"]) + "\n")
            self.proc.stdin.close()
            self.proc.wait()


def serve(chip, fin=sys.stdin, fout=sys.stdout):
    for line in fin:
        f = line.split()
        if not f:
            continue
        if f[0] == "S":
            chip.set_inputs(ui_in=int(f[1], 16), uio_in=int(f[2], 16), rst_n=int(f[3], 16))
        elif f[0] == "T":
            chip.tick(int(f[1], 16))
        elif f[0] == "R":
            fout.write("%x\n" % chip.uo_out)
            fout.flush()
        elif f[0] == "Q":
            break


if __name__ == "__main__":
    from .model import Chip
    serve(Chip(seed=int(sys.argv[1]) if len(sys.argv) > 1 else 0))
