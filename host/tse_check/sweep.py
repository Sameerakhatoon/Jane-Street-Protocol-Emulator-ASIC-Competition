"""Margin sweep: rerun a capture at stepped timing offsets, find the failing edge.

capture(offset) runs one capture with the swept parameter moved by offset
ticks (a board session rewriting one configuration word, a simulation, or a
model) and returns its record words. Each step is checked against the
obligations; a step fails when any obligation is not a clean pass, or when
the optional ok(events) callback says the device misbehaved. The result
names the last passing offset, the first failing one, and the edge that
failed there: the obligation and its worst measurement.
"""
from . import records, report


class Step:
    __slots__ = ("offset", "results", "ok", "errors")

    def __init__(self, offset, results, ok):
        self.offset, self.results, self.ok = offset, results, ok
        self.errors = sum(r.count(report.VIOLATION) + r.count(report.POSSIBLE) for r in results)


class Sweep:
    def __init__(self, steps):
        self.steps = steps
        self.last_pass = self.first_fail = None
        for s in steps:
            if s.ok:
                self.last_pass = s.offset
            else:
                self.first_fail = s.offset
                break

    def failing_edge(self):
        """(obligation name, Measurement or None) at the first failing step."""
        for s in self.steps:
            if s.ok:
                continue
            for r in s.results:
                if r.verdict() != report.PASS:
                    return r.ob.name, r.worst()
            return "device", None
        return None


def run(capture, obligations, offsets, ok=None, stop=True):
    """Sweep the offsets in order; with stop, end at the first failing step."""
    steps = []
    for off in offsets:
        events, _ = records.decode(capture(off))
        results = report.check(events, obligations)
        good = report.overall(results) == report.PASS and (ok is None or ok(events))
        steps.append(Step(off, results, good))
        if stop and not good:
            break
    return Sweep(steps)


def lines(sw, clock_hz, bar=40):
    out = ["%8s %6s  %s" % ("offset", "errors", "result")]
    for s in sw.steps:
        out.append("%8d %6d  %-4s %s" % (s.offset, s.errors, "ok" if s.ok else "FAIL",
                                         "#" * min(bar, s.errors)))
    out.append("")
    out.append("last passing offset: %s" % ("none" if sw.last_pass is None else "%d t" % sw.last_pass))
    out.append("first failing offset: %s" % ("none" if sw.first_fail is None else "%d t" % sw.first_fail))
    edge = sw.failing_edge()
    if edge:
        name, m = edge
        if m is None:
            out.append("failing edge: %s" % name)
        else:
            out.append("failing edge: %s, lane %d, t=%d to %d, measured %d t (%s), margin %s t"
                       % (name, m.lane, m.t0, m.t1, m.ticks, report.fmt_time(m.ticks, clock_hz),
                          "-" if m.margin is None else "%+g" % m.margin))
    return out
