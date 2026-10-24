"""Timing compliance of decoded events against obligations.

An interval obligation reads "after event A, event B follows no sooner than
min and no later than max ticks". Each A (re)starts a measurement, the next
B on a matching lane ends it, as the monitor lane does with its clock C.
When one bus is watched by several lanes (one lane sees the clock, another
the START and STOP conditions), same_lane=False pairs A and B from any
lane, and cancel codes end an open measurement without a verdict (a STOP
cancels "SCL rise to repeated START"). A forbid obligation names a code
that must never appear, such as a monitor's violation event; its data byte
is the measured ticks the lane saw.

Both edges of a measurement are asynchronous inputs sampled 0 to 1 tick
late, so a measured n ticks means a true interval strictly between n - 1 and
n + 1. A measurement is a certain violation when that whole interval breaks
the window, possible when only part of it does, and passes otherwise. The
margin is the measured distance to the nearer limit, negative when outside.
"""
from .records import DATA_BITS

PASS, POSSIBLE, VIOLATION = "pass", "possible", "VIOLATION"
_RANK = {PASS: 0, POSSIBLE: 1, VIOLATION: 2}
SATURATED = (1 << DATA_BITS) - 1


class Obligation:
    def __init__(self, name, start=None, end=None, min=None, max=None, lane=None, forbid=None,
                 same_lane=True, cancel=()):
        if forbid is None and (start is None or end is None):
            raise ValueError("%s: an interval obligation needs start and end codes" % name)
        if forbid is None and min is None and max is None:
            raise ValueError("%s: give min, max or both" % name)
        if min is not None and max is not None and min > max:
            raise ValueError("%s: min %s above max %s" % (name, min, max))
        if forbid is not None and (cancel or not same_lane):
            raise ValueError("%s: cancel and same_lane apply to intervals only" % name)
        self.name, self.start, self.end = name, start, end
        self.min, self.max, self.lane, self.forbid = min, max, lane, forbid
        self.same_lane, self.cancel = same_lane, tuple(cancel)

    def lane_ok(self, e):
        return self.lane is None or e.lane == self.lane


class Measurement:
    __slots__ = ("t0", "t1", "lane", "ticks", "verdict", "margin", "saturated")

    def __init__(self, t0, t1, lane, ticks, verdict, margin, saturated=False):
        self.t0, self.t1, self.lane, self.ticks = t0, t1, lane, ticks
        self.verdict, self.margin, self.saturated = verdict, margin, saturated


def judge(ticks, lo=None, hi=None):
    """(verdict, margin) of a measured tick count against a window [lo, hi]."""
    verdict, margin = PASS, None
    if lo is not None:
        margin = ticks - lo
        if ticks + 1 <= lo:
            verdict = VIOLATION
        elif ticks - 1 < lo:
            verdict = POSSIBLE
    if hi is not None:
        m = hi - ticks
        margin = m if margin is None else min(margin, m)
        if ticks - 1 >= hi:
            verdict = VIOLATION
        elif ticks + 1 > hi and verdict == PASS:
            verdict = POSSIBLE
    return verdict, margin


class Result:
    """Measurements of one obligation and their summary."""

    def __init__(self, ob):
        self.ob, self.meas, self.open = ob, [], 0

    def verdict(self):
        if not self.meas:
            return PASS
        return max((m.verdict for m in self.meas), key=_RANK.get)

    def worst(self):
        """The measurement with the smallest margin (the first such one)."""
        if not self.meas:
            return None
        return min(self.meas, key=lambda m: (m.margin is None, m.margin if m.margin is not None else 0))

    def count(self, verdict):
        return sum(1 for m in self.meas if m.verdict == verdict)


def check(events, obligations):
    """Result per obligation for a list of events in time order."""
    results = []
    for ob in obligations:
        r = Result(ob)
        if ob.forbid is not None:
            for e in events:
                if e.code == ob.forbid and ob.lane_ok(e):
                    m = judge(e.data, ob.min, ob.max)[1]
                    r.meas.append(Measurement(e.ts, e.ts, e.lane, e.data, VIOLATION, m,
                                              e.data == SATURATED))
        else:
            started = {}
            for e in events:
                if not ob.lane_ok(e):
                    continue
                key = e.lane if ob.same_lane else None
                if e.code == ob.end and key in started:
                    t0 = started.pop(key)
                    v, m = judge(e.ts - t0, ob.min, ob.max)
                    r.meas.append(Measurement(t0, e.ts, e.lane, e.ts - t0, v, m))
                if e.code in ob.cancel:
                    started.pop(key, None)
                if e.code == ob.start:
                    started[key] = e.ts
            r.open = len(started)
        results.append(r)
    return results


def overall(results):
    return max((r.verdict() for r in results), key=_RANK.get, default=PASS)


def fmt_time(ticks, clock_hz):
    s = ticks / clock_hz
    if abs(s) >= 1e-3:
        return "%.3f ms" % (s * 1e3)
    if abs(s) >= 1e-6:
        return "%.3f us" % (s * 1e6)
    return "%.1f ns" % (s * 1e9)


def _limit(ob, clock_hz):
    parts = []
    if ob.min is not None:
        parts.append("min %s" % fmt_time(ob.min, clock_hz))
    if ob.max is not None:
        parts.append("max %s" % fmt_time(ob.max, clock_hz))
    return "  ".join(parts)


def lines(results, clock_hz):
    """Report text: one line per non-passing measurement, then a summary table."""
    out = []
    flagged = sorted(((m.t1, r, m) for r in results for m in r.meas if m.verdict != PASS),
                     key=lambda x: x[0])
    for t, r, m in flagged:
        meas = ("at least " if m.saturated else "") + fmt_time(m.ticks, clock_hz)
        out.append("t=%s  %s  %s  measured %s  %s  (lane %d)"
                   % ("{:,}".format(t), m.verdict, r.ob.name, meas, _limit(r.ob, clock_hz), m.lane))
    if flagged:
        out.append("")
    nw = max([12] + [len(r.ob.name) for r in results])
    out.append("%-*s %6s %6s %6s %10s %10s %10s  %s"
               % (nw, "obligation", "count", "poss", "viol", "worst", "margin", "window", "verdict"))
    for r in results:
        w = r.worst()
        worst = "-" if w is None else "%d t" % w.ticks
        margin = "-" if w is None or w.margin is None else "%+g t" % w.margin
        lo = "-" if r.ob.min is None else "%g" % r.ob.min
        hi = "-" if r.ob.max is None else "%g" % r.ob.max
        out.append("%-*s %6d %6d %6d %10s %10s %10s  %s"
                   % (nw, r.ob.name, len(r.meas), r.count(POSSIBLE), r.count(VIOLATION),
                      worst, margin, "%s..%s" % (lo, hi), r.verdict()))
    return out
