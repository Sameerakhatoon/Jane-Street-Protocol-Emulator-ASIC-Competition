"""Stimulus strategies shared by the block lockstep tests."""
import os
import warnings

from hypothesis import strategies as st
from hypothesis.errors import HypothesisSideeffectWarning

warnings.filterwarnings("ignore", category=HypothesisSideeffectWarning)

NC = int(os.environ.get("BLOCK_CYCLES", "160"))

bit = st.integers(0, 1)


def rare(one_in):
    return st.integers(0, one_in - 1).map(lambda v: int(v == 0))


def often(one_in):
    return st.integers(0, one_in - 1).map(lambda v: int(v != 0))


def stream(ports, n=NC):
    """n cycles; ports maps a port name to the strategy of its per-cycle value."""
    return st.lists(st.fixed_dictionaries(ports), min_size=n, max_size=n)


def levels(names, n=NC, max_run=12):
    """Piecewise-constant levels for the named 1-bit ports: n cycles."""
    seg = st.tuples(st.integers(1, max_run), st.integers(0, (1 << len(names)) - 1))

    def expand(segs):
        out = []
        for length, v in segs:
            out += [v] * length
        out += [out[-1]] * max(0, n - len(out))
        return [{k: (v >> j) & 1 for j, k in enumerate(names)} for v in out[:n]]
    return st.lists(seg, min_size=1, max_size=n // 2).map(expand)


def merge(*streams):
    return st.tuples(*streams).map(lambda ss: [dict(kv for s in parts for kv in s.items())
                                               for parts in zip(*ss)])
