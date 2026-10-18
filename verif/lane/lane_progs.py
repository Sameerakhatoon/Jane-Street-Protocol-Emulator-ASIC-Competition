"""Hypothesis strategies for lane lockstep runs: configuration preambles, bodies, waves.

Each profile adds one lane feature to a core that the RTL implements,
so a failing test points at one difference.
"""
from hypothesis import strategies as st

from model.isa import encode as E
from progs import DLY, IN, OUT, SET_REG, _ss, mbox, mov, set_pins, wait

N = 16
SAFE_CSRR = [0x20, 0x21, 0x22, 0x25]          # idle values agree; STATUS, CLOCK, TS have tests
SET_CMD = st.builds(lambda ss, v: E("SET", ss, dst=7, imm=v), _ss(), st.sampled_from([0, 1]))
SET_SAFE = st.builds(lambda ss, d, i: E("SET", ss, dst=d, imm=i), _ss(), st.sampled_from([2, 3, 4, 5]),
                     st.integers(0, 255))
CSRR_SAFE = st.builds(lambda ss, a: E("CSRR", ss, addr=a), _ss(), st.sampled_from(SAFE_CSRR))
CORE = [set_pins(False), SET_SAFE, SET_CMD, OUT, IN, mbox(False), DLY, CSRR_SAFE,
        mov(range(7), pc_dst=False)]
CORE_WAIT = {0: range(4), 1: [0, 1, 2, 3, 4, 5, 6, 7], 2: [1, 2, 3, 4, 5, 7]}


def body(extra=(), core=CORE, n=N):
    park = E("JMP", cond=0, target=n - 1)
    return st.lists(st.one_of(*core, *extra), min_size=n - 1, max_size=n - 1).map(lambda p: p + [park])


def no_cfg():
    return st.just([])


# LANE_CFG: ss_base [5:4], side_en [6], jmp_pin [8:7]; nothing that starts an assist
LANE_CFG = st.builds(lambda b, s, j: [(0x18, b << 4 | s << 6 | j << 7)], st.integers(0, 3),
                     st.integers(0, 1), st.integers(0, 3))
JMP_PIN = st.builds(lambda ss, t: E("JMP", ss, cond=5, target=t), _ss(), st.integers(0, N - 1))

DATA = [st.builds(lambda ss, op, a: E(op, ss, ind=0, addr=a), _ss(), st.sampled_from(["LDW", "STW"]),
                  st.integers(0, 127)),
        st.builds(lambda ss, op: E(op, ss, ind=1, addr=0), _ss(), st.sampled_from(["LDW", "STW"]))]

# timer: counts cycles from a reload of 1 to 12, TIMER replaces event 0
TIMER_CFG = st.builds(lambda r: [(0x15, r | 1 << 17 | 1 << 18)], st.integers(1, 12))
WAIT_TIMER = st.builds(lambda ss: E("WAIT", ss, pol=1, src=1, idx=0), _ss())
FRAME_RST = st.builds(lambda ss: E("SET", ss, dst=7, imm=1), _ss())

# rules in pin-edge mode: clk_pin 1, rxpin 0, txpin 2; rule 1 chained on rule 0
RULES_CFG = st.builds(
    lambda edge, p0, m0, p1, m1, r1: [
        (0x18, 0 << 2 | 2), (0x48, 1 | 1 << 1 | edge << 3),
        (0x40, m0 << 16 | p0), (0x41, 1 << 24 | 0x0005), (0x42, 0x1400 | 0x2),
        (0x44, m1 << 16 | p1), (0x45, 1 << 24 | r1), (0x46, 0x3400 | 0x1)],
    st.integers(0, 1), st.integers(0, 0xFFFF), st.sampled_from([0x3, 0x7, 0xF]),
    st.integers(0, 0xFFFF), st.sampled_from([0x1, 0x3]), st.integers(0, 0xFFFF))

ILLEGAL = st.sampled_from([0xC000 | 0x0100 | 0x80 | 0x05,     # PUSH/PULL group reserved bits
                           E("OUT", dst=5) | 0x0600,          # OUT selector 7
                           E("IN") | 0x0600,                  # IN selector 6
                           E("SIG") | 0x0100])                # SIG reserved bit 8
# LDW X and STW X with a nonzero address field
ILLEGAL_DATA = st.sampled_from([E("LDW", ind=1, addr=0) | 0x01, E("STW", ind=1, addr=0) | 0x01])

# WAIT pairs: a WAIT at an even pc and a filler in the same word, so the
# successor never needs a fetch and the WAIT fetch timing stays out of the test
SET_T0 = st.just(E("SET", dst=4, imm=0))
SET_T = st.builds(lambda v: E("SET", dst=4, imm=v), st.integers(0, 6))
WAIT_FLAGGED = wait({0: range(4), 2: [7]}).filter(lambda w: (w >> 3) & 3)   # to or alt set
FILLER = st.builds(lambda i: E("SET", dst=2, imm=i), st.integers(0, 255))


def wait_pairs(pairs=4):
    """SET T, 0; SET T, v; WAIT (even pc); filler (same word), repeated, then park."""
    quad = st.tuples(SET_T0, SET_T, WAIT_FLAGGED, FILLER).map(list)
    return st.lists(quad, min_size=pairs, max_size=pairs).map(
        lambda qs: [w for q in qs for w in q] + [E("JMP", cond=0, target=4 * pairs)])


CLOCK = st.builds(lambda ss, a: E("CSRR", ss, addr=a), _ss(), st.sampled_from([0x26, 0x28]))
WAIT_SYNCED = st.builds(lambda ss, pol, to: E("WAIT", ss, pol=pol, src=2, idx=6, to=to), _ss(),
                        st.integers(0, 1), st.integers(0, 1))

# wave: (cycles, pins, host TX write, host RX read) segments
waves = st.lists(st.tuples(st.integers(1, 12), st.integers(0, 15), st.integers(0, 1), st.integers(0, 1)),
                 min_size=1, max_size=24)


def expand(wave, n):
    """Repeat the wave until n cycles: the preamble takes up to 130 cycles, and
    pin-edge rules need edges after it."""
    out = []
    for length, pins, txw, rxr in wave:
        out += [(pins, txw, rxr)] * length
    return (out * (n // len(out) + 1))[:n]
