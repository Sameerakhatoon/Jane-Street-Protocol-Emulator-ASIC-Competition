# SPDX-License-Identifier: Apache-2.0
"""03: instruction effects and costs."""
from model.isa import encode as E
from model.tests.helpers import SeqCfg, SeqIn, dispatch_cycles, parked, run_seq


def gaps(disp):
    d = dispatch_cycles(disp)
    return [b[0] - a[0] for a, b in zip(d, d[1:])]


def test_set_masks_and_t_shift():
    s, _, _ = run_seq(parked([E("SET", dst=0, imm=0x3F), E("SET", dst=0, imm=0x20),
                              E("SET", dst=4, imm=0x12), E("SET", dst=4, imm=0x34),
                              E("SET", dst=2, imm=0xCD), E("SET", dst=5, imm=0xAB)]), 20)
    assert s.pins == 0x1 and s.t == 0x1234 and s.x == 0xABCD


def test_jmp_xdec_always_decrements():
    s, _, _ = run_seq(parked([E("SET", dst=2, imm=2), E("JMP", cond=2, target=1)]), 30)
    assert s.x == 0xFFFF                     # taken at 2 and 1, falls through at 0, then 0 - 1


def test_out_in_shift():
    s, _, _ = run_seq(parked([E("SET", dst=2, imm=0xA5), E("MOV", dst=5, src=0),
                              E("OUT", dst=3, n1=3), E("IN", src=4, n1=1),
                              E("IN", src=2, n1=3)]), 20)
    assert s.y == 0x5 and s.osr == 0xA and s.isr == (0x3 << 4) | 0x5


def test_mov_pc_and_notx():
    s, _, disp = run_seq(parked([E("SET", dst=2, imm=6), E("MOV", dst=7, src=0), E("DLY"),
                                 E("DLY"), E("DLY"), E("DLY"), E("MOV", dst=1, src=6)]), 30)
    assert 2 not in disp and s.y == 0xFFF9


def test_csrw_costs_three_for_class_l():
    _, _, disp = run_seq(parked([E("CSRW", addr=0x18), E("CSRW", addr=0x20),
                                 E("CSRW", addr=0x48), E("DLY", d=8)]), 20)
    assert gaps(disp)[:3] == [3, 1, 3]


def test_csrr_class_l_reads_zero():
    rd = lambda a: 0xDEADBEEF
    s, _, _ = run_seq(parked([E("CSRR", addr=0x18)]), 10, csr_read=rd)
    assert (s.y, s.x) == (0, 0)
    s, _, _ = run_seq(parked([E("CSRR", addr=0x23)]), 10, csr_read=rd)
    assert (s.y, s.x) == (0xDEAD, 0xBEEF)


def test_pull_push_mailboxes():
    s, _, _ = run_seq(parked([E("PULL", blk=1), E("MOV", dst=4, src=4), E("PUSH")]), 20,
                      data=[0x1234])
    assert s.rx_full and s.rx_data == 0x1234 and s.isr == 0
    s, _, _ = run_seq(parked([E("SET", dst=2, imm=7), E("PULL")]), 10)
    assert s.osr == 7                        # non-blocking PULL on empty gives X


def test_blocking_pull_waits_for_host():
    ins = lambda c: SeqIn(h_tx_we=int(c == 20), h_tx_data=0x99)
    s, _, disp = run_seq(parked([E("PULL", blk=1), E("DLY", d=20)]), 40, ins=ins)
    assert s.osr == 0x99 and dispatch_cycles(disp)[1][0] == 22


def test_ldw_stw_data_slots():
    dwin = [0] * 128
    dwin[5] = 0xCAFE
    s, _, disp = run_seq(parked([E("LDW", addr=5), E("MOV", dst=4, src=4), E("STW", addr=6),
                                 E("DLY", d=30)]), 60, lane=1, dwin=dwin)
    d = dispatch_cycles(disp)
    assert d[0][0] == 2 and d[1][0] == 7 + 2    # lane 1 data slot 7: LDW lasts 7 + 2 - 2
    assert s.isr == 0xCAFE
    assert d[2][0] == 10 and d[3][0] == 23 + 1  # MOV misses; slot 23: STW lasts 23 + 1 - 10
    assert dwin[6] == 0xCAFE


def test_tmr_guard_exact():
    _, _, disp = run_seq(parked([E("TMR", cclr=1, gwe=1, gsel=1), E("WAIT", pol=1, src=2, idx=0),
                                 E("DLY", d=30)]), 40, cfg=SeqCfg(gt=(0, 10, 0, 0)))
    d = dispatch_cycles(disp)
    assert d[2][0] == d[0][0] + 1 + 10 + 1      # wait ends in c + 1 + GT1, successor one later


def test_side_set_wraps_modulo_four():
    s, _, _ = run_seq(parked([E("DLY", ss=3)]), 2, cfg=SeqCfg(side_en=1, ss_base=3))
    assert s.pins == 0b1001


def test_illegal_word_halts():
    s, _, disp = run_seq(parked([E("DLY"), 0x8600, E("DLY")]), 20)
    assert s.halted and s.illegal and dispatch_cycles(disp)[-1][1] == 1
