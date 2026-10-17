"""12: DPLL: free-running strobes, fractional periods, hard sync, lock and drift."""
import random

from model.dpll import Dpll, DpllCfg, DpllIn


def free_run(cfg, cycles):
    d, outs = Dpll(cfg), []
    for _ in range(cycles):
        outs.append(d.step(DpllIn(enable=1)))
    return outs


def cycles_of(outs, key):
    return [c for c, o in enumerate(outs) if o[key]]


def test_01_strobes_once_per_bit_at_their_points():
    outs = free_run(DpllCfg(period=960, spt=768), 600)
    bs = cycles_of(outs, "bit_start")
    assert [b - a for a, b in zip(bs, bs[1:])] == [60] * (len(bs) - 1)
    for key, at in (("q1", 15), ("half", 30), ("sample", 48)):
        got = cycles_of(outs, key)
        assert [c - b for b, c in zip([-1] + bs, got)] [1:] == [at + 1] * (len(got) - 1), key


def test_02_fractional_period_is_exact_on_average():
    outs = free_run(DpllCfg(period=40, spt=20), 1000)          # 2.5 cycles per bit
    bs = cycles_of(outs, "bit_start")
    gaps = {b - a for a, b in zip(bs, bs[1:])}
    assert gaps == {2, 3} and abs((bs[-1] - bs[0]) / (len(bs) - 1) - 2.5) < 0.01


def test_03_disabled_is_silent_and_restarts_at_zero():
    d = Dpll(DpllCfg(period=160, spt=80))
    for _ in range(5):
        o = d.step(DpllIn(enable=0))
        assert not any(o[k] for k in ("bit_start", "q1", "half", "sample"))
    assert d.ph == 0


def test_04_hard_sync_starts_the_bit_on_the_armed_edge():
    d = Dpll(DpllCfg(period=320, spt=160, fall=1))
    d.step(DpllIn(enable=1, hard_arm=1, s_p=1))
    outs = [d.step(DpllIn(enable=1, s_p=1)) for _ in range(7)]
    assert not any(o["sample"] or o["q1"] for o in outs)      # armed: an idle line is not sampled
    o = d.step(DpllIn(enable=1, s_p=0))
    assert o["bit_start"] and o["edge_seen"] and not d.armed
    outs = [d.step(DpllIn(enable=1, s_p=0)) for _ in range(20)]
    assert cycles_of(outs, "sample")[0] == 9                  # first phase at or past 10 cycles


def transmit(bits, period, offset, cfg):
    """Line driven by a transmitter whose bit k starts at offset + k * period
    (real cycles); returns the values sampled at each SAMPLE strobe."""
    d = Dpll(cfg)
    got, n = [], int(offset + len(bits) * period)
    level = lambda t: bits[min(int((t - offset) // period), len(bits) - 1)] if t >= offset else 1
    for c in range(n):
        o = d.step(DpllIn(enable=1, s_p=level(c), s_n=level(c - 0.5)))
        if o["sample"]:
            got.append((c, o["samp_val"]))
    return got


def test_05_lock_from_any_phase():
    cfg = DpllCfg(period=16 * 20, spt=16 * 10, sjw=16 * 2, resync=1, rise=1, fall=1)
    bits = [k & 1 for k in range(60)]
    for offset in range(0, 20, 3):
        got = transmit(bits, 20, offset + 0.25, cfg)
        tail = got[-20:]
        phase = [(c - offset) % 20 for c, _ in tail]
        assert len(set(phase)) == 1 and 8 <= phase[0] <= 12, (offset, phase)


def test_06_one_percent_drift_never_loses_a_bit():
    """60 and 10 cycles per bit with single-edge input, 2.5 with dual edge, sender 1 percent off."""
    rng = random.Random(7)
    bits = [rng.randint(0, 1) for _ in range(400)]
    for cycles, dual in ((60, 0), (10, 0), (2.5, 1)):
        p = round(16 * cycles)
        cfg = DpllCfg(period=p, spt=p // 2, sjw=max(4, p // 8), resync=1, dual=dual, rise=1, fall=1)
        for drift in (1.01, 0.99):
            vals = [v for _, v in transmit(bits, cycles * drift, 3.3, cfg)]
            body = bits[5:-5]
            assert any(vals[k:k + len(body)] == body for k in range(12)), (cycles, drift)


def test_07_restart_mid_bit_keeps_every_strobe():
    cfg = DpllCfg(period=320, spt=240)
    d = Dpll(cfg)
    for _ in range(17):
        d.step(DpllIn(enable=1))                           # past the sample point of bit 0
    d.step(DpllIn(enable=0))
    outs = [d.step(DpllIn(enable=1)) for _ in range(20)]
    for key in ("q1", "half", "sample"):
        assert len(cycles_of(outs, key)) == 1, key
