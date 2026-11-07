# SPDX-License-Identifier: Apache-2.0
"""Check the judge cases in manifest.txt before any demo trace exists.

Every case is named after a spec in specs/. For every case: the expect file
has frame keys, the options parse with the judges' own command line, and
every mapped signal is a wire of the spec (demos dump pins under their wire
names). For UART, SPI, CAN, PS/2 and 10BASE-T a trace is then written from
the expected keys with the judges' test waveform writer, at the rate and
mode the spec itself states (its baud, bitrate or clock param, the mode in
its name), and the judge must pass it with the case's options and expect
file. So a manifest whose rate, mode or pin names disagree with the spec
fails here, before a demo is blamed for it.

    python3 specs/judge/selfcheck.py [manifest]
"""
import os
import re
import shlex
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "verif", "judges", "tests"))

from tools.tsec.parser import parse         # noqa: E402
from tools.tsec.resolve import resolve       # noqa: E402
from verif.judges import cli                 # noqa: E402
import jwave                                 # noqa: E402


def cases(path):
    out = []
    with open(path) as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, proto, vcd, expect, *opts = shlex.split(line)
            out.append((n, name, proto, expect, [o for o in opts if o != "--allow-skip"]))
    return out


def spec_facts(name):
    """(wire names, {param: float}, SPI mode) of specs/NAME.tse."""
    with open(os.path.join(ROOT, "specs", name + ".tse")) as f:
        r = resolve(parse(f.read()))
    sym = r[0] if isinstance(r, tuple) else r
    params = {k: float(q.v) for k, (_, q) in sym.params.items()}
    m = re.search(r"mode(\d)", name)
    return set(sym.wires), params, int(m.group(1)) if m else 0


def maps(opts):
    return dict(opts[k + 1].split("=", 1) for k, o in enumerate(opts) if o == "--map")


def hexbytes(s):
    return [int(x, 16) for x in s.split(",")] if s else []


def wave_uart(keys, m, p, mode):
    w = jwave.Wave()
    for sig in m.values():
        w.add(sig, 1)
    t = 10e-6
    for k in keys:
        role, _, val = k.rpartition(" ")
        t = jwave.uart_tx(w, m[role or "rx"], t, int(val, 16), p["baud"]) + 20e-6
    return w


def wave_spi(keys, m, p, mode):
    w = jwave.Wave()
    for role in ("cs", "clk", "mosi", "miso"):
        w.add(role, {"cs": 1, "clk": mode >> 1}.get(role, 0))
    t = 1e-6
    for k in keys:
        f = dict(x.split("=") for x in k.split())
        t = jwave.spi_xfer(w, t, hexbytes(f.get("mosi")), hexbytes(f.get("miso")), mode=mode,
                           period=1 / p.get("f_sck", 1e6)) + 2e-6
    w.sigs = {m.get(r, r): v for r, v in w.sigs.items()}
    return w


def wave_can(keys, m, p, mode):
    rate = p["bitrate"]
    w = jwave.Wave()
    w.add(m["rx"], 1)
    t = 20 / rate
    for k in keys:
        ident, _, data = k.partition("#")
        t = jwave.can_wave(w, m["rx"], t, jwave.can_bits(int(ident, 16), bytes.fromhex(data)), rate)
    return w


def wave_ps2(keys, m, p, mode):
    half = 1 / (2 * p["f_clk"]) if "f_clk" in p else 40e-6
    w = jwave.Wave()
    w.add("clk", 1)
    w.add("data", 1)
    t = 100e-6
    for k in keys:
        who, val = k.split()
        f = jwave.ps2_dev if who == "dev" else jwave.ps2_host
        t = f(w, t, int(val, 16), half=half) + 200e-6
    w.sigs = {m.get(r, r): v for r, v in w.sigs.items()}
    return w


def wave_eth10(keys, m, p, mode):
    w = jwave.Wave()
    w.add(m["td"], 0)
    t = 1e-6
    for k in keys:
        dst, src, etype, payload = k.split()
        bits = jwave.eth_frame_bits(bytes.fromhex(dst), bytes.fromhex(src), int(etype, 16),
                                    bytes.fromhex(payload))
        t = jwave.eth_wave(w, m["td"], t, bits, scale=10e6 / p["bitrate"]) + 10e-6
    return w


WAVES = {"uart": wave_uart, "spi": wave_spi, "can": wave_can, "ps2": wave_ps2, "eth10": wave_eth10}


def check(line, name, proto, exp, opts, tmp):
    """Problems of one case, as a list of strings."""
    if not os.path.exists(os.path.join(ROOT, "specs", name + ".tse")):
        return ["no spec named %s" % name]
    keys = cli.read_expect(exp) if os.path.exists(exp) else []
    if not keys:
        return ["no frame keys in %s" % os.path.basename(exp)]
    try:
        cli.parser().parse_args([proto, "x.vcd"] + opts)
    except SystemExit:
        return ["options do not parse"]
    wires, params, mode = spec_facts(name)
    m = maps(opts)
    stray = sorted(s for s in m.values() if s not in wires)
    if stray:
        return ["mapped signal(s) %s are not wires of the spec" % ", ".join(stray)]
    if proto not in WAVES:
        return []
    trace = WAVES[proto](keys, m, params, mode).write(os.path.join(tmp, name + ".vcd"), tail=50e-6)
    with open(os.devnull, "w") as quiet:
        st = cli.run([proto, trace, "--expect", exp] + opts, out=quiet)
    return [] if st == 0 else ["the judge fails a trace made from the spec and %s" % os.path.basename(exp)]


def main(argv):
    path = argv[1] if len(argv) > 1 else os.path.join(HERE, "manifest.txt")
    base = os.path.dirname(os.path.abspath(path))
    bad = judged = n = 0
    with tempfile.TemporaryDirectory() as tmp:
        for line, name, proto, expect, opts in cases(path):
            n += 1
            probs = check(line, name, proto, os.path.join(base, expect), opts, tmp)
            for pr in probs:
                print("line %d %s: %s" % (line, name, pr))
            bad += bool(probs)
            judged += proto in WAVES and not probs
    print("selfcheck: %d cases, %d judged on a synthetic trace, %d with problems" % (n, judged, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
