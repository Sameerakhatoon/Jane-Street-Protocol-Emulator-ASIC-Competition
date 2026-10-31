"""Serial-port client for the UART pty bridge, standing in for picocom.

usage: pty_client.py SEND_HEX EXPECT_HEX -- SIM_BINARY [SIM_ARGS...]
       pty_client.py SEND_HEX EXPECT_HEX --pty /dev/pts/N [--timeout S]

Starts the simulator, reads its 'pty /dev/pts/N' line, opens that path
raw like a serial terminal, writes SEND, reads until len(EXPECT) bytes
arrive or the timeout (10 s) passes, stops the simulator and compares.
With --pty it opens a pty that is already there (a simulator started by
someone else, for example in pin-server mode) and leaves it running.
Exit 0 on match.
"""
import os
import select
import subprocess
import sys
import time
import tty


def main():
    send = bytes.fromhex(sys.argv[1])
    want = bytes.fromhex(sys.argv[2])
    args = sys.argv[3:]
    limit = 10.0
    if "--timeout" in args:
        k = args.index("--timeout")
        limit = float(args[k + 1])
        del args[k:k + 2]
    sim = None
    if args[:1] == ["--pty"]:
        path = args[1]
    else:
        sim = subprocess.Popen(args[args.index("--") + 1:], stdout=subprocess.PIPE, text=True)
        line = sim.stdout.readline().split()
        if len(line) != 2 or line[0] != "pty":
            sim.kill()
            print("no pty line from simulator:", line)
            return 1
        path = line[1]
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
    tty.setraw(fd)
    t0 = time.time()
    os.write(fd, send)
    got = b""
    while len(got) < len(want) and time.time() - t0 < limit:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            got += os.read(fd, 256)
    dt = time.time() - t0
    os.close(fd)
    if sim:
        sim.terminate()
        sim.wait(timeout=10)
    print(f"pty {path}: sent {len(send)} bytes, got {got!r} in {dt:.3f} s")
    return 0 if got == want else 1


if __name__ == "__main__":
    sys.exit(main())
