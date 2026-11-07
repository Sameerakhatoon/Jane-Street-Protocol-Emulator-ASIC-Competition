# SPDX-License-Identifier: Apache-2.0
"""Frames, timing violations and the small helpers every decoder shares.

A decoder returns a Result: the frames it decoded, each with a canonical
key string (what the expect mode compares), and the violations it found,
each with the measured value and the limit it broke. Times inside a
decoder are file time units; Result converts to seconds for reports.
"""

EPS = 1e-9      # relative slack so a waveform exactly at a limit passes


class Frame:
    __slots__ = ("t0", "t1", "key", "info")

    def __init__(self, t0, t1, key, info=""):
        self.t0 = t0
        self.t1 = t1
        self.key = key
        self.info = info


class Violation:
    __slots__ = ("t", "rule", "measured", "limit", "unit", "text")

    def __init__(self, t, rule, measured, limit, unit="", text=""):
        self.t = t
        self.rule = rule
        self.measured = measured
        self.limit = limit
        self.unit = unit
        self.text = text


class Result:
    def __init__(self, trace):
        self.trace = trace
        self.frames = []
        self.violations = []

    def frame(self, t0, t1, key, info=""):
        self.frames.append(Frame(t0, t1, key, info))

    def violate(self, t, rule, measured, limit, unit="", text=""):
        self.violations.append(Violation(t, rule, measured, limit, unit, text))

    def keys(self):
        return [f.key for f in self.frames]

    def rules(self):
        return [v.rule for v in self.violations]

    def check_min(self, t, rule, units, limit_s, text=""):
        """Violation if the interval (file units) is shorter than limit_s seconds."""
        s = self.trace.seconds(units)
        if s < limit_s * (1 - EPS) - 1e-18:
            self.violate(t, rule, fmt_time(s), ">= " + fmt_time(limit_s), "", text)

    def check_range(self, t, rule, units, lo_s, hi_s, text=""):
        s = self.trace.seconds(units)
        if s < lo_s * (1 - EPS) or s > hi_s * (1 + EPS):
            self.violate(t, rule, fmt_time(s), "%s to %s" % (fmt_time(lo_s), fmt_time(hi_s)), "", text)

    def sort(self):
        self.frames.sort(key=lambda f: f.t0)
        self.violations.sort(key=lambda v: v.t)
        return self


def fmt_time(s):
    a = abs(s)
    if a >= 1e-3:
        return "%.6g ms" % (s * 1e3)
    if a >= 1e-6:
        return "%.6g us" % (s * 1e6)
    return "%.6g ns" % (s * 1e9)


def fit_slope(points):
    """Least-squares slope of t over k for [(k, t)], or None with fewer than two k."""
    n = len(points)
    if n < 2:
        return None
    mk = sum(k for k, _ in points) / n
    mt = sum(t for _, t in points) / n
    sxx = sum((k - mk) ** 2 for k, _ in points)
    if sxx == 0:
        return None
    return sum((k - mk) * (t - mt) for k, t in points) / sxx


def rate_check(res, t0, edges, period, tol_pct, rule, unit_name, nominal):
    """Fit edge times to a bit grid starting at t0 and flag a rate error.

    edges are change times inside one frame; period is the nominal bit time
    in file units; nominal is the nominal rate (for the message).
    Returns the measured period or None.
    """
    # bit numbers counted edge to edge, so a rate error that adds up to more
    # than half a bit over a long frame is not rounded away
    pts = [(0, t0)]
    for e in edges:
        k = pts[-1][0] + round((e - pts[-1][1]) / period)
        if k > pts[-1][0]:
            pts.append((k, e))
    slope = fit_slope(pts)
    if slope is None or slope <= 0:
        return None
    dev = period / slope - 1.0
    if abs(dev) * 100.0 > tol_pct * (1 + EPS):
        res.violate(t0, rule, "%.6g %s (%+.2f%%)" % (nominal * (1 + dev), unit_name, dev * 100.0),
                    "%g %s +/- %g%%" % (nominal, unit_name, tol_pct))
    return slope


def parity_ok(bits, kind):
    ones = sum(bits) & 1
    return ones == 0 if kind == "even" else ones == 1
