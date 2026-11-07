# SPDX-License-Identifier: Apache-2.0
"""Hypothesis strategies for lockstep runs: programs, lane setup, input waveforms.

A profile names the instruction forms a test may use. "straight", "branch"
and "wait" stay inside what the RTL implements; each remaining
profile adds one instruction form whose RTL behaviour differs from the ISA,
so a failing test points at one difference.
"""
import warnings

from hypothesis.errors import HypothesisSideeffectWarning
from hypothesis import strategies as st

from model.isa import CLASS_L, encode as E

warnings.filterwarnings("ignore", category=HypothesisSideeffectWarning)   # cocotb imports via pytest

N = 16                                       # instructions per program (8 SRAM words)
CSRW_CORE = sorted(a for a in CLASS_L if a < 0x20) + list(range(0x20, 0x29))
CSRR_ANY = [a for a in range(0x20, 0x40)]


def _ss():
    return st.integers(0, 3)


def jmp(conds):
    return st.builds(lambda ss, c, t: E("JMP", ss, cond=c, target=t),
                     _ss(), st.sampled_from(conds), st.integers(0, N - 1))


def wait(srcs):
    pairs = [(s, i) for s, idxs in srcs.items() for i in idxs]
    return st.builds(lambda ss, si, pol, to, alt, d: E("WAIT", ss, pol=pol, src=si[0], idx=si[1],
                                                       to=to, alt=alt, delay=d),
                     _ss(), st.sampled_from(pairs), st.integers(0, 1), st.integers(0, 1),
                     st.integers(0, 1), st.integers(0, 7))


def set_pins(full_mask):
    imm = st.integers(0, 255) if full_mask else st.integers(0, 15).map(lambda v: 0xF0 | v)
    return st.builds(lambda ss, d, i: E("SET", ss, dst=d, imm=i), _ss(), st.integers(0, 1), imm)


SET_REG = st.builds(lambda ss, d, i: E("SET", ss, dst=d, imm=i), _ss(), st.integers(2, 7),
                    st.integers(0, 255))
OUT = st.builds(lambda ss, d, n, dl: E("OUT", ss, dst=d, n1=n, delay=dl), _ss(),
                st.integers(0, 5), st.integers(0, 31), st.integers(0, 7))
IN = st.builds(lambda ss, s, n, dl: E("IN", ss, src=s, n1=n, delay=dl), _ss(),
               st.integers(0, 5), st.integers(0, 31), st.integers(0, 7))
def mbox(blocking):
    return st.builds(lambda ss, op, b: E(op, ss, blk=b), _ss(), st.sampled_from(["PUSH", "PULL"]),
                     st.integers(0, int(blocking)))
CSRR = st.builds(lambda ss, a: E("CSRR", ss, addr=a), _ss(), st.sampled_from(CSRR_ANY))
DLY = st.builds(lambda ss, d: E("DLY", ss, d=d), _ss(),
                st.one_of(st.integers(0, 6), st.sampled_from([15, 40])))


def mov(srcs, pc_dst=True):
    dsts = list(range(8 if pc_dst else 7))
    return st.builds(lambda ss, d, s, dl: E("MOV", ss, dst=d, src=s, delay=dl & 7),
                     _ss(), st.sampled_from(dsts), st.sampled_from(srcs), st.integers(0, 31))


def csrw(addrs):
    return st.builds(lambda ss, a: E("CSRW", ss, addr=a), _ss(), st.sampled_from(addrs))


CORE_WAIT = {0: range(4), 1: range(8), 2: [1, 2, 3, 4, 5, 6, 7]}

PROFILES = {
    # no control flow: every word is fetched once, in order
    "straight": [set_pins(False), SET_REG, OUT, IN, mbox(False), CSRR, DLY, csrw(CSRW_CORE),
                 mov(range(7), pc_dst=False)],
    # plus jumps and computed jumps: words are revisited, the buffer replacement matters
    "branch": [set_pins(False), SET_REG, OUT, IN, mbox(False), DLY, mov(range(7)),
               jmp(range(7)), jmp([0])],
    # straight line plus instructions that can stall on an input: WAIT (pins, events,
    # DPLL and mailbox flags, alternate, timeout) and blocking PUSH and PULL
    "wait": [set_pins(False), SET_REG, OUT, DLY, mbox(True), wait(CORE_WAIT)],
}

BASE = [SET_REG, OUT, DLY]

TMR = st.builds(lambda ss, c, g, gs, d: E("TMR", ss, cclr=c, gwe=g, gsel=gs, delay=d), _ss(),
                st.integers(0, 1), st.integers(0, 1), st.integers(0, 3), st.integers(0, 7))
SIG = st.builds(lambda ss, m, v: E("SIG", ss, mask=m, val=v), _ss(), st.integers(0, 15),
                st.integers(0, 15))
EMIT = st.builds(lambda ss, c, d: E("EMIT", ss, code=c, delay=d), _ss(), st.integers(0, 0x3D),
                 st.integers(0, 7))

# one profile per instruction form the RTL does not implement as specified yet
PROFILES.update({
    "set_mask": BASE + [set_pins(True)],
    "jmp_guard": BASE + [st.builds(lambda ss: E("JMP", ss, cond=7, target=N - 1), _ss())],
    "wait_edge_chip_guard": BASE + [wait({0: range(4, 8), 2: [0], 3: range(8)})],
    "mov_clock": BASE + [mov([7], pc_dst=False)],
    "tmr_sig_emit": BASE + [TMR, SIG, EMIT],
    "csr_classes": BASE + [csrw([0x05, 0x06, 0x0A, 0x1F, 0x40, 0x41, 0x42, 0x48])],
})


def programs(profile):
    """N instructions; all but "branch" end in a jump to itself, so no word is fetched twice."""
    body = st.one_of(*PROFILES[profile])
    if profile == "branch":
        return st.lists(body, min_size=N, max_size=N)
    park = E("JMP", cond=0, target=N - 1)
    return st.lists(body, min_size=N - 1, max_size=N - 1).map(lambda p: p + [park])


def setups(ss_bases=(0, 1, 2)):
    return st.fixed_dictionaries(dict(
        lane=st.integers(0, 1), side_en=st.integers(0, 1), ss_base=st.sampled_from(ss_bases),
        jmp_pin=st.integers(0, 3), alt_pol=st.integers(0, 1),
        alt=st.sampled_from([(s, i) for s, idxs in CORE_WAIT.items() for i in idxs])))


# piecewise-constant input waveform: (cycles, value); value bits [3:0] pins,
# [11:4] ev, [15:12] DPLL flags, [16] host writes TX mailbox, [17] host reads RX
waves = st.lists(st.tuples(st.integers(1, 12), st.integers(0, (1 << 18) - 1)),
                 min_size=1, max_size=16)


def expand(wave, n):
    out = []
    for length, v in wave:
        out += [v] * length
    out += [out[-1]] * max(0, n - len(out))
    return out[:n]


def csr_stub(addr):
    """The harness's CSR read data, mirrored for the model."""
    return (addr << 24) | ((~addr & 0xFF) << 16) | ((addr ^ 0x5A) << 8) | 0xC3
