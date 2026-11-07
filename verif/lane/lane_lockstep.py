# SPDX-License-Identifier: Apache-2.0
"""Run tse_lane (through lane_harness) and model.lane.Lane side by side.

A program is a configuration preamble followed by a body. The preamble
writes each configuration word with CSRW, so both sides learn their
configuration through the instruction set and the harness never needs the
RTL's bank layout. The assists stay idle unless the program starts them,
so the model's assist inputs are the idle values: TXNEED, CRCOK and TXIDLE
high, SYNCED high, no strobes.
"""
from cocotb.triggers import ReadOnly, RisingEdge

from model.isa import decode, disasm, encode as E, pack
from model.lane import Lane, LaneIn

IDLE_EV = 0x49                    # TXNEED, CRCOK, TXIDLE
IDLE_FLG = 1 << 6                 # SYNCED: the DPLL is not waiting for a hard sync
REGS = ("pins", "dirs", "x", "y", "t", "isr", "osr", "tflag", "aflag", "h_tx_full", "h_rx_full",
        "irq")
TX_DATA = lambda c: (0x5A000000 | (c * 0x9E37)) & 0xFFFFFFFF
DWIN = [(0x0D000000 | k * 0x01010101) & 0xFFFFFFFF for k in range(128)]


def load_word(addr, v):
    """Six instructions that write v to lane register addr."""
    y, x = v >> 16, v & 0xFFFF
    return [E("SET", dst=2, imm=y & 0xFF), E("SET", dst=5, imm=y >> 8), E("MOV", dst=1, src=0),
            E("SET", dst=2, imm=x & 0xFF), E("SET", dst=5, imm=x >> 8), E("CSRW", addr=addr)]


def relocate(body, off):
    """Shift JMP targets by off (bodies use no computed jumps)."""
    out = []
    for w in body:
        try:
            name, ss, f = decode(w)
        except Exception:
            out.append(w)
            continue
        if name == "JMP":
            w = E("JMP", ss, cond=f["cond"], target=(f["target"] + off) & 0xFF)
        out.append(w)
    return out


def assemble(cfg, body):
    pre = [w for addr, v in cfg for w in load_word(addr, v)]
    return pre + relocate(body, len(pre))


def model_regs(m):
    s = m.seq
    return dict(pins=m.vout, dirs=m.voe, x=s.x, y=s.y, t=s.t, isr=s.isr, osr=s.osr, tflag=s.tflag,
                aflag=s.aflag, h_tx_full=s.tx_full, h_rx_full=s.rx_full, irq=s.irq)


def rtl_regs(dut):
    v = lambda sig: int(sig.value)
    fl = v(dut.h_flags)
    return dict(pins=v(dut.vout), dirs=v(dut.voe), x=v(dut.x), y=v(dut.y), t=v(dut.t),
                isr=v(dut.isr), osr=v(dut.osr), tflag=v(dut.tflag), aflag=v(dut.aflag),
                h_tx_full=fl & 1, h_rx_full=fl >> 1, irq=v(dut.irq))


def listing(prog):
    return "\n".join(f"  {i:3d}: {w:#06x}  {disasm(w)}" for i, w in enumerate(prog))


async def load(dut, prog, lane):
    dut.run.value = 0
    dut.rst_n.value = 0
    for sig in (dut.cfg_clr, dut.prog_we, dut.dwin_we, dut.h_tx_we, dut.h_rx_re, dut.h_re, dut.pins):
        sig.value = 0
    dut.h_addr.value = 0x23
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    dut.cfg_clr.value = 1
    await RisingEdge(dut.clk)
    dut.cfg_clr.value = 0
    words = pack(prog) + [0] * (128 - (len(prog) + 1) // 2)
    for a, w in enumerate(words):
        dut.prog_we.value, dut.prog_addr.value, dut.prog_data.value = 1, a, w
        dut.dwin_we.value, dut.dwin_addr.value, dut.dwin_data.value = 1, a, DWIN[a]
        await RisingEdge(dut.clk)
    dut.prog_we.value = dut.dwin_we.value = 0
    dut.lane.value = lane
    await RisingEdge(dut.clk)
    return words


async def run_lane(dut, prog, lane, wave, ncycles, status=False):
    """wave: per-cycle (pins, host writes TX mailbox, host reads RX mailbox).
    With status, the host also reads STATUS every cycle and it is compared.
    Returns None if RTL and model agree, else a report."""
    words = await load(dut, prog, lane)
    m = Lane(words, lane, dwin=list(DWIN))
    dut.h_re.value = int(status)
    trace = []
    dut.run.value = 1
    for c in range(ncycles):
        pins, txw, rxr = wave[min(c, len(wave) - 1)]
        i = LaneIn(pins=pins, ev_ext=IDLE_EV, flg=IDLE_FLG, crc_ok=1, tx_idle=1, h_tx_we=txw,
                   h_tx_data=TX_DATA(c), h_rx_re=rxr)
        dut.pins.value, dut.h_tx_we.value, dut.h_tx_data.value = pins, txw, i.h_tx_data
        dut.h_rx_re.value = rxr
        await ReadOnly()
        want, got = model_regs(m), rtl_regs(dut)
        if status:
            m._ev = m.events(IDLE_EV)
            want["status"], got["status"] = m.status(), int(dut.h_rdata.value)
        r_disp = int(dut.pc.value) if int(dut.dispatch.value) else None
        o = m.step(i)
        await RisingEdge(dut.clk)
        trace.append((c, o.dispatch, r_disp))
        diffs = [k for k in want if want[k] != got[k]]
        if o.dispatch != r_disp:
            diffs.insert(0, "dispatch")
        if diffs:
            dut.run.value = 0
            k = diffs[0]
            detail = (f"model dispatches pc {o.dispatch}, RTL pc {r_disp}" if k == "dispatch"
                      else f"model {want[k]:#x}, RTL {got[k]:#x}")
            recent = " ".join(f"{cc}:{a}/{b}" for cc, a, b in trace[-10:]
                              if a is not None or b is not None)
            return (f"first divergence in cycle {c}, signal {k}: {detail}; also {diffs[1:]}\n"
                    f"recent dispatches cycle:model/rtl: {recent}\n"
                    f"lane {lane}\nprogram:\n{listing(prog)}")
    dut.run.value = 0
    return None
