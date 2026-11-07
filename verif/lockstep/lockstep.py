# SPDX-License-Identifier: Apache-2.0
"""Run tse_seq (through seq_harness) and model.seq.Seq side by side, cycle by cycle.

In each cycle the inputs are applied after the clock edge, the RTL is read in
ReadOnly and compared with the model's state for that cycle (registers) and
with the model's outputs of that cycle (dispatch, CSR write, command), then
the model steps. The first difference is returned with a short trace.
"""
from cocotb.triggers import ReadOnly, RisingEdge

from model.isa import disasm, pack
from model.seq import Seq, SeqCfg, SeqIn
from progs import csr_stub, expand

REGS = ("pins", "dirs", "x", "y", "t", "isr", "osr", "tflag", "aflag", "h_tx_full", "h_rx_full",
        "irq")
TX_DATA = lambda c: (0xA5000000 | (c * 0x9E37)) & 0xFFFFFFFF


def model_regs(s):
    return dict(pins=s.pins, dirs=s.dirs, x=s.x, y=s.y, t=s.t, isr=s.isr, osr=s.osr,
                tflag=s.tflag, aflag=s.aflag, h_tx_full=s.tx_full, h_rx_full=s.rx_full,
                irq=s.irq)


def rtl_regs(dut):
    v = lambda sig: int(sig.value)
    return dict(pins=v(dut.pin_out) & 0xF, dirs=v(dut.pin_oe) & 0xF, x=v(dut.x), y=v(dut.y),
                t=v(dut.t), isr=v(dut.isr), osr=v(dut.osr), tflag=v(dut.tflag),
                aflag=v(dut.aflag), h_tx_full=v(dut.h_tx_full), h_rx_full=v(dut.h_rx_full),
                irq=v(dut.irq))


def listing(prog):
    return "\n".join(f"  {i:2d}: {w:#06x}  {disasm(w)}" for i, w in enumerate(prog))


async def load(dut, prog, setup):
    dut.run.value = 0
    dut.rst_n.value = 0
    for sig in (dut.prog_we, dut.h_tx_we, dut.h_rx_re, dut.pins, dut.ev, dut.dpll):
        sig.value = 0
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    words = pack(prog) + [0] * (128 - (len(prog) + 1) // 2)
    for a, w in enumerate(words):
        dut.prog_we.value, dut.prog_addr.value, dut.prog_data.value = 1, a, w
        await RisingEdge(dut.clk)
    dut.prog_we.value = 0
    src, idx = setup["alt"]
    dut.alt_cfg.value = (idx << 3) | (src << 1) | setup["alt_pol"]
    dut.lane.value = setup["lane"]
    dut.side_en.value = setup["side_en"]
    dut.ss_base.value = setup["ss_base"]
    dut.jmp_pin.value = setup["jmp_pin"]
    await RisingEdge(dut.clk)
    return words


async def run_lockstep(dut, prog, setup, wave, ncycles):
    """Returns None if RTL and model agree for ncycles, else a report string."""
    words = await load(dut, prog, setup)
    src, idx = setup["alt"]
    cfg = SeqCfg(side_en=setup["side_en"], ss_base=setup["ss_base"], jmp_pin=setup["jmp_pin"],
                 alt_pol=setup["alt_pol"], alt_src=src, alt_idx=idx)
    m = Seq(words, setup["lane"], cfg, csr_read=csr_stub)
    trace = []
    dut.run.value = 1
    for c, v in enumerate(expand(wave, ncycles)):
        i = SeqIn(pins=v & 0xF, ev=(v >> 4) & 0xFF, flg=((v >> 12) & 0x7) << 1 | ((v >> 15) & 1) << 6,
                  h_tx_we=(v >> 16) & 1, h_tx_data=TX_DATA(c), h_rx_re=(v >> 17) & 1)
        dut.pins.value, dut.ev.value, dut.dpll.value = i.pins, i.ev, (v >> 12) & 0xF
        dut.h_tx_we.value, dut.h_tx_data.value, dut.h_rx_re.value = i.h_tx_we, i.h_tx_data, i.h_rx_re
        await ReadOnly()
        want, got = model_regs(m), rtl_regs(dut)
        rd = int(dut.dispatch.value)
        r_disp = int(dut.pc.value) if rd else None
        r_csr = (int(dut.csr_addr.value), int(dut.csr_wdata.value)) if int(dut.csr_we.value) else None
        o = m.step(i)
        await RisingEdge(dut.clk)
        trace.append((c, o.dispatch, r_disp))
        diffs = [k for k in REGS if want[k] != got[k]]
        if o.dispatch != r_disp:
            diffs.insert(0, "dispatch")
        if o.csr_we != r_csr:
            diffs.append("csr_we")
        if diffs:
            dut.run.value = 0
            k = diffs[0]
            if k == "dispatch":
                detail = f"model dispatches pc {o.dispatch}, RTL pc {r_disp}"
            elif k == "csr_we":
                detail = f"model {o.csr_we}, RTL {r_csr}"
            else:
                detail = f"model {want[k]:#x}, RTL {got[k]:#x}"
            recent = " ".join(f"{cc}:{a}/{b}" for cc, a, b in trace[-10:] if a is not None or b is not None)
            return (f"first divergence in cycle {c}, signal {k}: {detail}; also {diffs[1:]}\n"
                    f"recent dispatches cycle:model/rtl: {recent}\n"
                    f"lane {setup['lane']}, setup {setup}\nprogram:\n{listing(prog)}")
    dut.run.value = 0
    return None
