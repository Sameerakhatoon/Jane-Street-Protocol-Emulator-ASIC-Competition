"""`tsec selftest`: the ISA tables and the cycle model against the published ISA numbers.

It pins the seam in isa.py: a swap to
the generated tables must still give these encodings and these cycle counts.
"""
from . import isa
from .timing import FetchModel, base_duration, first_slot, next_dispatch

SAMPLES = [("JMP", dict(cond="XDEC", target=6, ss=1), 0x0A06),
           ("OUT", dict(dst="PINS", n=1, delay=6), 0x8006),
           ("EMIT", dict(code=5, delay=2), 0xE62A),
           ("WAIT", dict(pol=1, src="PIN", idx=5, alt=1), 0x24A8),
           ("LDW", dict(ind=1), 0xC780)]
UART = [("SET", dict(dst="PINS", imm=0x11)), ("SET", dict(dst="PINDIRS", imm=0xFD)),
        ("PULL", dict(blk=1)), ("SET", dict(dst="X", imm=7)),
        ("SET", dict(dst="PINS", imm=0x10, ss=1)), ("DLY", dict(d=6, ss=1)),
        ("OUT", dict(dst="PINS", n=1, delay=6, ss=1)), ("JMP", dict(cond="XDEC", target=6, ss=1)),
        ("SET", dict(dst="PINS", imm=0x11)), ("JMP", dict(cond="ALWAYS", target=2))]
UART_WORDS = [0x4011, 0x41FD, 0xC300, 0x4207, 0x4810, 0xE806, 0x8806, 0x0A06, 0x4011, 0x0002]
SQUARE = [("SET", dict(dst="PINDIRS", imm=0xF3)), ("DLY", dict(d=0)), ("DLY", dict(d=4, ss=1)),
          ("DLY", dict(d=2, ss=0)), ("JMP", dict(cond="ALWAYS", target=2, ss=0)), ("DLY", dict(d=0))]


def pin_runs(prog, lane, fm, frames, bit=0, ss_base=2, side=True, max_cycles=400):
    """Free-running executor for small programs; returns (level, cycles) runs of one pin."""
    pc, x, osr, pins = 0, 0, 0, 0
    c, buf, data, tr = first_slot(0, fm.S, lane) + fm.lat, [0], list(frames), []
    while c < max_cycles:
        mn, f = prog[pc]
        npc, new = pc + 1, pins
        if mn == "SET" and f["dst"] in ("PINS", "X"):
            if f["dst"] == "X":
                x = f["imm"]
            else:
                m = f["imm"] >> 4
                new = (pins & ~m) | (f["imm"] & m)
        elif mn == "PULL":
            if not data:
                break
            osr = data.pop(0)
        elif mn == "OUT":
            new = (pins & ~1) | (osr & 1)
            osr >>= 1
        elif mn == "JMP":
            taken = f["cond"] == "ALWAYS" or (f["cond"] == "XDEC" and x != 0)
            if f["cond"] == "XDEC":
                x = (x - 1) & 0xFFFF
            if taken:
                npc = f["target"]
        if side and "ss" in f:
            m = 3 << ss_base
            new = (new & ~m) | (f["ss"] << ss_base)
        if ((new ^ pins) >> bit) & 1:
            tr.append((c + 1, (new >> bit) & 1))
        pins = new
        c, pc = next_dispatch(c, base_duration(mn, f, c, lane, fm), npc, buf, lane, fm), npc
    return [(v, t1 - t0) for (t0, v), (t1, _) in zip(tr, tr[1:])]


def checks():
    """[(description, passed)]."""
    out = []
    for mn, f, want in SAMPLES:
        got = isa.encode(mn, f)
        out.append((f"{isa.fmt(mn, f)} = 0x{got:04X} (expected 0x{want:04X})", got == want))
    words = [isa.encode(mn, f) for mn, f in UART]
    out.append(("UART example encodes to " + " ".join(f"{w:04X}" for w in words), words == UART_WORDS))
    legal, per, round_trip = 0, {}, True
    for w in range(1 << 16):
        r = isa.decode(w)
        if r is None:
            continue
        legal += 1
        per[r[0]] = per.get(r[0], 0) + 1
        round_trip &= isa.encode(*r) == w
    out.append((f"all 65536 words: {legal} legal over {len(per)} instructions, each re-encodes "
                "identically, the rest illegal", len(per) == 16 and round_trip))
    fm = FetchModel()
    lane0 = pin_runs(UART, 0, fm, [0x55, 0xA3])
    bits, frame = set(n for _, n in lane0[1:-4]), sum(n for _, n in lane0[1:11])
    out.append((f"UART layout 2, lane 0: bits {sorted(bits)} cycles, frame {frame} cycles "
                "(expected 8 and 80)", bits == {8} and frame == 80))
    for e, want in ((1, [(1, 5), (0, 7)]), (2, [(1, 5), (0, 4)])):
        sq = pin_runs(SQUARE, 1, FetchModel(entries=e), [], ss_base=0, max_cycles=80)[2:4]
        out.append((f"square wave with {e} buffer word(s): {sq} (expected {want})", sq == want))
    ld = sorted({base_duration("LDW", {}, c, 0, fm) for c in range(16)})
    st = sorted({base_duration("STW", {}, c, 0, fm) for c in range(16)})
    out.append((f"LDW lasts {ld[0]} to {ld[-1]}, STW {st[0]} to {st[-1]} cycles over the 16 "
                "data-frame phases (expected 2 to 17, 1 to 16)",
                (ld[0], ld[-1], st[0], st[-1]) == (2, 17, 1, 16)))
    out.append(("CSRW LANE_CFG (class L) costs 3 cycles, CSRW TXDATA (class F) 1",
                base_duration("CSRW", {"addr": 0x18}, 0, 0, fm) == 3
                and base_duration("CSRW", {"addr": 0x20}, 0, 0, fm) == 1))
    return out


def main():
    print("self-test: encoder, decoder and timing model against the published ISA numbers")
    results = checks()
    for name, ok in results:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    ok = all(r for _, r in results)
    print("self-test " + ("passed" if ok else "FAILED"))
    return 0 if ok else 3
