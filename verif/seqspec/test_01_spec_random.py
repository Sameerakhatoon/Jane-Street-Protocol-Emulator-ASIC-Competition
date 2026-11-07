# SPDX-License-Identifier: Apache-2.0
"""The sequencer spec agrees cycle by cycle with the ISA cost model on random legal programs.

Every test runs the spec for lanes 0 to 3 at fetch latency 1 and 2 on one
random 256-word program, one random input waveform and one random start
configuration, and compares every field of every cycle with seqspec_ref.
SEQSPEC_TESTS and SEQSPEC_CYCLES scale the run (defaults 40 and 2000); tests
are generated, compared and dropped one at a time.
"""
import os
import random

import pytest

import seqspec_gen as gen
import seqspec_ref as ref
import seqspec_sim as sim

TESTS = int(os.environ.get("SEQSPEC_TESTS", 40))
CYCLES = int(os.environ.get("SEQSPEC_CYCLES", 2000))
SEED = int(os.environ.get("SEQSPEC_SEED", 1))

pytestmark = pytest.mark.skipif(not sim.have_verilator(), reason="needs verilator")


def make_case(n):
    rng = random.Random(SEED * 100003 + n)
    return gen.rand_prog(rng), gen.rand_env(rng, CYCLES), gen.rand_cfg(rng)


def count(seen, key, n=1):
    seen[key] = seen.get(key, 0) + n


def cover(seen, prog, rows):
    for r in rows:
        if r["issue"]:
            ins = gen.isa.decode(prog[r["pc"]])
            key = ins.name
            if ins.name == "CSRW" and gen.regs.is_cfg(ins.fields["addr"]):
                key = "CSRW_RULE" if ins.fields["addr"] >= 0x40 else "CSRW_CFG"
            count(seen, key)
    for a, b in zip(rows, rows[1:]):
        count(seen, "alt", a["aflag"] < b["aflag"])
        count(seen, "timeout", a["tflag"] < b["tflag"])
    count(seen, "halt", rows[-1]["halted"])


# 1: every copy of every test matches the reference in every cycle, and the random
# programs reach every instruction, CSRW to rule and other configuration words,
# alternate and timeout endings and halts
def test_01_traces_match(tmp_path):
    for n in range(TESTS):
        gen.write_test(tmp_path, n, *make_case(n))
    bad, seen = [], {}
    for n, tr in enumerate(sim.run(tmp_path, TESTS, CYCLES)):
        prog, env, cfg = make_case(n)
        for i in range(sim.COPIES):
            lane, lat = sim.lane_lat(i)
            diff = sim.first_diff(tr[i], ref.run(prog, env, lane, lat, *cfg, CYCLES))
            if diff:
                bad.append((n, lane, lat, diff))
            cover(seen, prog, tr[i])
    assert not bad, bad[:5]
    need = list(gen.isa.INSTRS) + ["CSRW_RULE", "CSRW_CFG", "alt", "timeout", "halt"]
    assert all(seen.get(k, 0) > 0 for k in need), {k: seen.get(k, 0) for k in need}
