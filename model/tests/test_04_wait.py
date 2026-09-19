"""04: WAIT with its own condition, the alternate condition and the timeout."""
import pytest

from model.isa import encode as E
from model.tests.helpers import SeqCfg, SeqIn, dispatch_cycles, parked, run_seq

# lane 0, WAIT at pc 0 dispatches in cycle 1; its successor shares word 0
ALT_PIN1_HIGH = SeqCfg(alt_pol=1, alt_src=0, alt_idx=1)


def wait_end(wait, ins, cfg=None, t=None):
    prog = ([E("SET", dst=4, imm=t >> 8), E("SET", dst=4, imm=t & 0xFF)] if t is not None else [])
    prog += [wait, E("DLY", d=40)]
    s, _, disp = run_seq(parked(prog), 120, cfg=cfg, ins=ins)
    d = dispatch_cycles(disp)
    k = len(prog) - 2
    return d[k][0], d[k + 1][0], s


def pin_at(n, bit=0):
    return lambda c: SeqIn(pins=(1 << bit) if c >= n else 0)


def test_already_true_lasts_one_plus_delay():
    c, nxt, _ = wait_end(E("WAIT", pol=1, src=2, idx=7, delay=5), None)
    assert nxt - c == 1 + 5


def test_level_wait_ends_in_the_cycle_it_is_seen():
    c, nxt, s = wait_end(E("WAIT", pol=1, src=0, idx=0, delay=2), pin_at(10))
    assert (c, nxt) == (1, 10 + 1 + 2) and not s.tflag and not s.aflag


def test_long_delay_idiom_is_t_plus_one_plus_delay():
    c, nxt, s = wait_end(E("WAIT", pol=0, src=2, idx=7, to=1, delay=7), None, t=50)
    assert nxt - c == 50 + 1 + 7 and s.tflag


def test_edge_wait_on_change_since_dispatch():
    ins = lambda c: SeqIn(pins=1 if 5 <= c < 30 else 0)
    c, nxt, _ = wait_end(E("WAIT", pol=1, src=0, idx=4), ins)
    assert (c, nxt) == (1, 6)              # pin 0 was 0 at dispatch, changes in cycle 5


def test_alternate_ends_wait_and_sets_aflag():
    c, nxt, s = wait_end(E("WAIT", pol=1, src=0, idx=0, alt=1), pin_at(9, bit=1), ALT_PIN1_HIGH)
    assert nxt == 10 and s.aflag and not s.tflag


@pytest.mark.parametrize("own, alt, want", [(1, 1, (0, 0)), (0, 1, (1, 0)), (0, 0, (0, 1))])
def test_priority_own_then_alternate_then_timeout(own, alt, want):
    # WAIT dispatched in cycle 5 with T = 9 times out in cycle 14, where both conditions rise
    ins = lambda c: SeqIn(pins=(own | alt << 1) if c >= 14 else 0)
    c, nxt, s = wait_end(E("WAIT", pol=1, src=0, idx=0, alt=1, to=1), ins, ALT_PIN1_HIGH, t=9)
    assert c == 5 and (s.aflag, s.tflag) == want and nxt == 15


def test_jmp_tout_branches_after_timeout():
    prog = parked([E("WAIT", pol=1, src=2, idx=0, to=1), E("JMP", cond=6, target=3),
                   E("SET", dst=2, imm=1), E("SET", dst=3, imm=1)])
    s, _, _ = run_seq(prog, 30, cfg=SeqCfg(gt=(0xFFFF, 0, 0, 0)))
    assert s.tflag and s.x == 0 and s.y == 1


def test_wait_successor_fetch_starts_at_dispatch():
    # WAIT at pc 1 (word 0), successor at pc 2 (word 1): fetched during the wait,
    # so a wait longer than 4 cycles has no stall
    prog = parked([E("DLY"), E("WAIT", pol=1, src=0, idx=0), E("DLY", d=40)])
    _, _, disp = run_seq(prog, 60, ins=pin_at(20))
    assert dispatch_cycles(disp)[2][0] == 21
