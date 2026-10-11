"""Serial-port client for the UART pty bridge, standing in for picocom.

usage: pty_client.py SEND_HEX EXPECT_HEX -- SIM_BINARY [SIM_ARGS...]

Starts the simulator, reads its 'pty /dev/pts/N' line, opens that path
raw like a serial terminal, writes SEND, reads until len(EXPECT) bytes
arrive or 10 s pass, stops the simulator and compares. Exit 0 on match.
"""
import os
import select
import subprocess
import sys
import time
import tty


def main():
    sep = sys.argv.index("--")
    send = bytes.fromhex(sys.argv[1])
    want = bytes.fromhex(sys.argv[2])
    cmd = sys.argv[sep + 1:]
    sim = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
    line = sim.stdout.readline().split()
    if len(line) != 2 or line[0] != "pty":
        sim.kill()
        print("no pty line from simulator:", line)
        return 1
    fd = os.open(line[1], os.O_RDWR | os.O_NOCTTY)
    tty.setraw(fd)
    t0 = time.time()
    os.write(fd, send)
    got = b""
    while len(got) < len(want) and time.time() - t0 < 10:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            got += os.read(fd, 256)
    dt = time.time() - t0
    os.close(fd)
    sim.terminate()
    sim.wait(timeout=10)
    print(f"pty {line[1]}: sent {len(send)} bytes, got {got!r} in {dt:.3f} s")
    return 0 if got == want else 1


if __name__ == "__main__":
    sys.exit(main())
