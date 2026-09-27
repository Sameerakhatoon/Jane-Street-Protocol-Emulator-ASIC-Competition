"""Test paths and a synthetic monitor that turns true edge times into records."""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.path.dirname(os.path.dirname(HERE))
ROOT = os.path.dirname(HOST)
for p in (HOST, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from tse_check import records  # noqa: E402

CLOCK = 60e6


def seen(t):
    """Tick at which an asynchronous edge at true time t (in ticks) is sampled:
    the first clock edge after it, so 0 to 1 tick late."""
    return math.floor(t) + 1


def capture(edges, lane=0):
    """Record words for [(true time in ticks, code, data)] as a monitor lane emits them."""
    evs = sorted((records.Event(seen(t), lane, code, data) for t, code, data in edges),
                 key=lambda e: e.ts)
    return records.encode(evs)
