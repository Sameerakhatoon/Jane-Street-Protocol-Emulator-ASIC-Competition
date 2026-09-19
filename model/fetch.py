"""Fixed-slot fetch and the two-word buffer of one lane.

Lane k owns the SRAM in every cycle s with s % S == k. The buffer keeps the
ENTRIES words used most recently; the word holding the executing instruction
is always one of them, so a miss replaces the other (least recently used).
lat is 1 (decode from A_DOUT) or 2 (registered A_DOUT, the timing fallback).
"""

S = 4
DATA_FRAME = 16


def first_owned(c, lane, period=S):
    return c + ((lane - c) % period)


def first_data_slot(c, lane):
    """Lane k's data cycles: s % 16 == 4k + 3."""
    return first_owned(c, 4 * lane + 3, DATA_FRAME)


class Fetch:
    def __init__(self, lane, entries=2, lat=1):
        self.lane, self.entries, self.lat = lane, entries, lat
        self.tags = []                       # word addresses, most recent first
        self.misses = 0

    def need(self, c, word):
        """Successor's word is needed by an instruction dispatched in cycle c.
        Returns the earliest cycle the successor can dispatch because of the fetch."""
        if word in self.tags:
            self.tags.remove(word)
            self.tags.insert(0, word)
            return c
        self.misses += 1
        del self.tags[self.entries - 1:]
        self.tags.insert(0, word)
        return first_owned(c, self.lane) + self.lat
