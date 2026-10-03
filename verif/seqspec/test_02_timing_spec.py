"""tools.tseasm.timing agrees with the sequencer spec on random straight-line blocks and loops.

Each case places a random block (or a loop body closed by JMP ALWAYS) at a
random start address and runs the spec from RUN for lanes 0 to 3 at fetch
latency 1 and 2. The dispatch cycles of the block, or the lengths of the
loop's iterations, must equal what timing.block() and timing.loop() predict.
WAIT and blocking PUSH/PULL are included with inputs that end them at once
(waits = 1).
"""
import os
import random

import pytest

import seqspec_gen as gen
import seqspec_sim as sim
from seqspec_ref import Env
from tools.tseasm import isa, timing

CASES = int(os.environ.get("SEQSPEC_TIMING_CASES", 60))
CYCLES = 1500
FILL = isa.encode("DLY", d=0)

pytestmark = pytest.mark.skipif(not sim.have_verilator(), reason="needs verilator")


def static_instr(rng):
    """A random instruction whose path and duration do not depend on data here."""
    while True:
        w = gen.rand_instr(rng)
        ins = isa.decode(w)
        if ins.name == "JMP" or (ins.name == "MOV" and ins.fields["dst"] == isa.ENUMS["movdst"]["PC"]):
            continue
        if ins.name == "WAIT":
            w = isa.encode("WAIT", pol=1, src="FLAG", idx=7, delay=ins.fields["delay"], ss=ins.fields["ss"])
        if ins.name == "DLY" and ins.fields["d"] > 40:
            continue
        return w


def make_case(rng, is_loop):
    start = rng.randint(0, 255)
    prog = [FILL] * 256
    n = rng.randint(1, 12 if is_loop else 40)
    pcs = [(start + i) & 0xFF for i in range(n)]
    for pc in pcs:
        prog[pc] = static_instr(rng)
    if is_loop:
        pcs.append((start + n) & 0xFF)
        prog[pcs[-1]] = isa.encode("JMP", target=start, ss=rng.randint(0, 3))
    return start, prog, pcs


# 1: dispatch cycles of random blocks and iteration lengths of random loops match the spec
def test_01_timing_matches_spec(tmp_path):
    rng = random.Random(int(os.environ.get("SEQSPEC_SEED", 1)) + 100)
    env = [Env(0, 0, 0, 0, 0, 1, 0)] * CYCLES        # mailboxes ready: PUSH/PULL end at once
    cases = []
    for n in range(CASES):
        case = make_case(rng, is_loop=n % 2 == 1)
        gen.write_test(tmp_path, n, case[1], env, (case[0], 0, 0, 0))
        cases.append(case)
    traces = sim.run(tmp_path, CASES, CYCLES)       # one test at a time
    checked, bad = 0, []
    for n, ((start, prog, pcs), tr) in enumerate(zip(cases, traces)):
        is_loop = n % 2 == 1
        waits = {pc: 1 for pc in pcs if isa.cost(isa.decode(prog[pc])).wait}
        for i in range(sim.COPIES):
            lane, lat = sim.lane_lat(i)
            st0 = timing.reset_state(lane, start, lat)
            got = [k for k, r in enumerate(tr[i]) if r["issue"]]
            if is_loop:
                lt = timing.loop(prog, pcs, lane, st0, waits=waits, fetch_latency=lat)
                heads = [k for k in got if tr[i][k]["pc"] == start][:len(got) // len(pcs)]
                got_len = [b - a for a, b in zip(heads, heads[1:])]
                want_len = [lt.iteration(j) for j in range(len(got_len))]
                ok = heads[0] == st0.cycle and got_len == want_len and len(got_len) >= 2
            else:
                r = timing.path(prog, pcs, lane, st0, waits=waits, fetch_latency=lat)
                want = r.dispatch + [r.end.cycle]
                ok = got[:len(want)] == want
            checked += 1
            if not ok:
                bad.append((n, lane, lat, pcs[0], len(pcs)))
    assert not bad, bad[:5]
    assert checked == CASES * sim.COPIES
