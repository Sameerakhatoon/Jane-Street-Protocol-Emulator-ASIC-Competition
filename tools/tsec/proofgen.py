"""Stage 7, proof generation: a SymbiYosys harness per compiled role, on the real lane RTL.

The harness top instantiates src/tse_lane.sv with what the chip gives it:
the configuration bank as a constant (the image's lane registers), a
program store answering fetches in the lane's slots (lane k when the
16-cycle count mod 4 is k, the word one cycle later), the data window in
the lane's data slot, a two-flop synchronizer on the pins, and free host
mailbox inputs. The host read port is held on STATUS, whose PC field tells
which instruction the lane is at; this is how the observers see the program
without reaching into the hierarchy.

Every OB-WIN obligation becomes one tse_window_check. Its trigger is the
synchronized trigger pin (what a WAIT sees), armed only while the lane is
at one of the waits for that event in the obligation's state, and has been
there for 5 cycles (longer than any fetch stall, so the WAIT has dispatched:
PC alone also shows an instruction still waiting for its word). Its response
is the cycle the lane leaves the acting instruction: with the instruction's
d extra cycles, the action happened in that cycle minus 1 + d. So with D
the cycles from the trigger being seen to the action (the compiler's
measure), the checker's age is D + 1 + d, and the pin-level window [a, b]
(pads d_min, d_max) holds when, for an output reaching the pins in
[D + 3, D + 4] and a sample in [D, D + 1]:
    LO = ceil(a - d_min - base) + 1 + d,   HI = floor(b - d_max - base - 1) + 1 + d,
base 3 for an output and 0 for a sample. QUIET is the least separation the
datasheet allows before the trigger, and the trigger holds until the
response (the checker's two assumptions, the environment's contract).
"""
import math
import os
import shutil
from fractions import Fraction as Fr

from . import cycles
from .semantics import base_event, min_separation, opposite

TEMPLATES = os.path.join(os.path.dirname(__file__), "proofs", "templates")
SRC = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
RTL = ["tse_isa_pkg.sv", "tse_regs_pkg.sv", "tse_seq.sv", "tse_dpll.sv", "tse_crc.sv",
       "tse_resp_rule.sv", "tse_stuffer.sv", "tse_codec.sv", "tse_nco.sv", "tse_timer.sv",
       "tse_lane.sv"]
BASE = {"out": 3, "in": 0}


def window_bounds(lo, hi, d_min=0, d_max=0, kind="in", extra=0):
    """Checker step bounds (LO, HI) for a pin-level window [lo, hi] in ticks."""
    base = BASE[kind]
    return max(1, math.ceil(lo - d_min - base) + 1 + extra), math.floor(hi - d_max - base - 1) + 1 + extra


class Ob:
    """One observed obligation: where it acts, what triggers it, where the lane waits for it."""

    def __init__(self, name, line, kind, lo, hi, act, extra, pin, val, waits, quiet):
        self.name, self.line, self.kind, self.lo, self.hi = name, line, kind, lo, hi
        self.act, self.extra, self.pin, self.val, self.waits, self.quiet = \
            act, extra, pin, val, waits, quiet


def extra_cycles(ins, lane, fm):
    return cycles.duration(ins.op, ins.a, 0, lane, fm) - 1


def frame_obs(sym, role, lane, low, labels, fm):
    """The paced-frame receiver: its start sample after the anchor edge."""
    a0 = labels["idle"]
    anchor = low.prog[a0]
    if anchor.op != "WAIT":
        return []
    quiet = 1               # armed at the anchor already says the last frame is over
    out = []
    for k, ins in enumerate(low.prog):
        ob = ins.ob
        if ob is None or ob.code != "OB-WIN":
            continue
        out.append(Ob(ob.name, ob.line, ob.kind, ob.window[0], ob.window[1], k,
                      extra_cycles(ins, lane, fm), anchor.a["idx"], anchor.a["pol"], [a0], quiet))
    return out


def fsm_obs(sym, role, lane, att, fm, fclk):
    """Every state-machine OB-WIN whose anchor is a rise or fall of an input pin."""
    states = sorted((att.labels[s.name.text], s.name.text) for s in role.states
                    if s.name.text in att.labels)

    def state_of(pc):
        return max((p for p in states if p[0] <= pc), default=(0, ""))[1]
    out = []
    for k, ins in enumerate(att.prog):
        ob = ins.ob
        if ob is None:
            continue
        base, _ = base_event(sym, ob.event)
        if base.kind not in ("rise", "fall"):
            continue
        wire, val = base.name.text, 1 if base.kind == "rise" else 0
        waits = [p for p, w in enumerate(att.prog) if w.wait is not None and w.wait.wire == wire
                 and state_of(p) == state_of(k)
                 and ((w.wait.kind == "edge" and w.wait.level in (val, None))
                      or (w.wait.kind == "pre" and w.wait.level == 1 - val))]
        if not waits:
            continue
        sep, _ = min_separation(sym, role, opposite(base), base, fclk)
        out.append(Ob(f"{ob.name} after {base.kind}({wire})", ob.span.line, ob.kind, ob.lo, ob.hi, k,
                      extra_cycles(ins, lane, fm), att.fsm.vin(wire), val,
                      waits, max(1, math.floor(sep))))
    return out


def harness(role, lane, obs, regs, words, window, pads):
    """(top module text, [(Ob, LO, HI)])."""
    rows = [(ob, *window_bounds(ob.lo, ob.hi, pads[0], pads[1], ob.kind, ob.extra)) for ob in obs]
    lines = top_lines(role, lane, regs, words, window, "one checker per OB-WIN obligation")
    for k, (ob, lo, hi) in enumerate(rows):
        en = "stay >= 3'd5 && (" + " || ".join(f"pc_q == 8'd{p}" for p in ob.waits) + ")"
        lines += [
            f"  // line {ob.line}: {ob.name} within [{float(ob.lo):.2f}, {float(ob.hi):.2f}] ticks "
            f"(instruction {ob.act}, {ob.extra} extra cycles)",
            f"  tse_window_check #(.LO({lo}), .HI({hi}), .QUIET({ob.quiet}), "
            f".W({max(hi, ob.quiet, 2).bit_length() + 1})) ob{k} (",
            f"    .clk, .rst(init), .en({en}), .trig_in(sync[{ob.pin}]), .trig_val(1'b{ob.val}),",
            f"    .resp_out(pc_q == 8'd{ob.act} && pc != 8'd{ob.act}), .resp_val(1'b1));",
        ]
    lines.append("endmodule")
    return "\n".join(lines) + "\n", rows


def top_lines(role, lane, regs, words, window, what):
    """The harness top up to its checkers: lane, configuration, program store, data window, the
    STATUS read port (pc, pc_q, stay)."""
    pc_hi, pc_lo = "tse_regs_pkg::LR_STATUS_PC_HI", "tse_regs_pkg::LR_STATUS_PC_LO"
    lines = [
        f"// top_{role.name.text}.sv: generated by tsec, do not edit.",
        f"// Role {role.name.text} on lane {lane}, the real tse_lane; {what}.",
        "module top (input logic clk, input logic [3:0] pin_in, input logic h_tx_we,",
        "            input logic [31:0] h_tx_data, input logic h_rx_re);",
        "  logic init = 1'b1;",
        "  always_ff @(posedge clk) init <= 1'b0;",
        "  logic rst_n, run;",
        "  assign rst_n = !init;",
        "  assign run = !init;",
        "  logic [3:0] s1, sync;                    // two-flop synchronizer, as in the chip",
        "  always_ff @(posedge clk) begin s1 <= pin_in; sync <= s1; end",
        "  logic [3:0] cnt = 4'd0;                  // the 16-cycle slot frame from RUN",
        "  always_ff @(posedge clk) cnt <= run ? cnt + 4'd1 : 4'd0;",
        "  logic [1023:0] cfg;",
        "  always_comb begin",
        "    cfg = '0;",
    ]
    for name, value in sorted(regs.items()):
        lines.append(f"    cfg[32*tse_regs_pkg::LW_{name} +: 32] = 32'h{value:08X};")
    lines += [
        "  end",
        "  logic imem_req, imem_gnt, dmem_req, dmem_we, dmem_gnt;",
        "  logic [6:0] imem_addr, dmem_addr;",
        "  logic [31:0] imem_rdata, dmem_wdata, dmem_rdata, h_rdata;",
        f"  assign imem_gnt = run && imem_req && cnt[1:0] == 2'd{lane};",
        f"  assign dmem_gnt = run && dmem_req && cnt == 4'd{4 * lane + 3};",
        "  function automatic logic [31:0] rom(input logic [6:0] a);",
        "    case (a)",
    ]
    for k in range(0, len(words), 2):
        hi = words[k + 1] if k + 1 < len(words) else 0
        lines.append(f"      7'd{k // 2}: rom = 32'h{hi:04X}{words[k]:04X};")
    lines += ["      default: rom = 32'h00000000;", "    endcase", "  endfunction"]
    used = max([a for a, _ in window], default=-1) + 1
    lines += ["  always_ff @(posedge clk) if (imem_gnt) imem_rdata <= rom(imem_addr);"]
    if used:                    # only the words the program uses: the rest read 0
        lines += [f"  logic [31:0] dwin [{used}];", "  initial begin",
                  f"    for (int i = 0; i < {used}; i++) dwin[i] = '0;"]
        for addr, value in window:
            lines.append(f"    dwin[{addr}] = 32'h{value:08X};")
        lines += [
            "  end",
            "  always_ff @(posedge clk) begin",
            f"    if (dmem_gnt && dmem_we && dmem_addr < 7'd{used}) dwin[dmem_addr] <= dmem_wdata;",
            f"    if (dmem_gnt && !dmem_we) dmem_rdata <= dmem_addr < 7'd{used} ? dwin[dmem_addr] : '0;",
            "  end",
        ]
    else:
        lines += ["  assign dmem_rdata = '0;"]
    lines += [
        "  logic [3:0] vout, voe, irq, sig_mask, sig_val;",
        "  logic [1:0] h_flags;",
        "  logic [31:0] h_rx_data;",
        "  logic [5:0] ev_code;",
        "  logic [7:0] ev_data;",
        "  logic ev_valid, halted, sig_we, cfg_we;",
        "  logic [4:0] cfg_word;",
        "  logic [31:0] cfg_wdata;",
        "  tse_lane #(.K(2), .PW(16), .V(4)) dut (",
        "    .clk, .rst_n, .run, .cfg, .cfg_we, .cfg_word, .cfg_wdata,",
        "    .h_we(1'b0), .h_re(1'b1), .h_addr(tse_regs_pkg::LR_STATUS), .h_wdata(32'd0), .h_rdata,",
        "    .h_tx_we, .h_tx_data, .h_rx_re, .h_rx_data, .h_flags,",
        "    .ev_valid, .ev_code, .ev_data, .irq, .halted, .sig_we, .sig_mask, .sig_val,",
        "    .imem_req, .imem_addr, .imem_gnt, .imem_rdata,",
        "    .dmem_req, .dmem_we, .dmem_addr, .dmem_wdata, .dmem_gnt, .dmem_rdata,",
        "    .chip_flags(6'd0), .ts(32'd0), .vin_p(sync), .vin_n(sync), .vout, .voe);",
        "  logic [7:0] pc, pc_q;",
        f"  assign pc = h_rdata[{pc_hi}:{pc_lo}];",
        "  always_ff @(posedge clk) pc_q <= pc;",
        "  logic [2:0] stay = 3'd0;                 // cycles the lane has been at pc_q",
        "  always_ff @(posedge clk) stay <= pc != pc_q ? 3'd0 : stay == 3'd7 ? stay : stay + 3'd1;",
    ]
    return lines


def sby_text(role_name, depth=40, bounded=False):
    """Prove with abc pdr, or (bounded) check every run of depth cycles from RUN with smtbmc:
    a paced frame's long delay loops keep pdr from converging in reasonable time."""
    files = [os.path.join(SRC, f) for f in RTL]
    reads = " ".join(os.path.basename(f) for f in files)
    prv = ["prv: mode bmc", f"prv: depth {depth}"] if bounded else ["prv: mode prove"]
    return "\n".join([
        "[tasks]", "prove prv", "cover cvr", "",
        "[options]", *prv, "cvr: mode cover", f"cvr: depth {depth}", "",
        "[engines]", "prv: " + ("smtbmc boolector" if bounded else "abc pdr"), "cvr: smtbmc boolector", "",
        "[script]", f"read_verilog -formal -sv {reads} tse_window_check.sv top_{role_name}.sv",
        "prep -top top", "flatten", "memory_map", "opt -fast", "techmap", "opt -fast",
        "setundef -undriven -anyseq", "setundef -zero", "opt -full", "opt_dff -sat", "opt_clean", "",
        "[files]", *files, "tse_window_check.sv", f"top_{role_name}.sv",
    ]) + "\n"


def write(outdir, role, lane, obs, regs, words, window, pads, bounded=False):
    """Write the harness of one role; returns the checker rows (empty: nothing observable)."""
    os.makedirs(outdir, exist_ok=True)
    shutil.copy(os.path.join(TEMPLATES, "tse_window_check.sv"), outdir)
    top, rows = harness(role, lane, obs, regs, words, window, pads)
    name = role.name.text
    with open(os.path.join(outdir, f"top_{name}.sv"), "w") as fh:
        fh.write(top)
    with open(os.path.join(outdir, f"{name}.sby"), "w") as fh:
        depth = max(40, max(hi for _, _, hi in rows) + 30) if bounded else 40
        fh.write(sby_text(name, depth, bounded))
    with open(os.path.join(outdir, f"{name}.hex"), "w") as fh:
        fh.write("".join(f"{w:04x}\n" for w in words))
    return rows


# ------------------------------------------------------------------ frame roles
# A frame role's obligations are about engine events and pins, not input edges:
#   feed   the instruction serving an engine event dispatches D cycles after the event is
#          seen, D in the analysis' range [vmin, vmax]. The event bit is on STATUS (what a
#          WAIT sees), so the checker's age is D + 1 + d as for a state machine;
#   place  pin to pin: the response pin changes ideal +/- (tolerance less the clock budget)
#          cycles after the trigger pin (or, after a `send`, after TX goes idle, which is the
#          cycle the line takes its idle level);
#   hold   a least distance (the pin the last instant drove keeps its level that long) has
#          no checker: it is listed with the obligations left out.
# An event the lane does not show on STATUS (the DPLL's half strobe) has no checker.
STATUS_EV = {"ev_TXNEED": 0, "rx": 1, "hit": 2}
CHECKERS = ("tse_window_check.sv",)


class Check:
    def __init__(self, kind, line, name, **kw):
        self.kind, self.line, self.name = kind, line, name
        self.__dict__.update(kw)


def marked(prog, mark, before=None, fw=False):
    """Program counters of the instructions that set mark (fw: the WAITs whose event sets it)."""
    end = len(prog) if before is None else before
    if fw:
        return [p for p in range(end) if getattr(getattr(prog[p], "fw", None), "mark", None) == mark]
    return [p for p in range(end) if getattr(prog[p], "mark", None) == mark]


def frame_checks(att, lane, fm, ppm):
    """(checks, obligations left out) of an accepted frame role (att: frametime.Attempt)."""
    low, prog, stats = att.low, att.prog, att.an.stats
    checks, skipped = [], []
    ev = "h_rdata[tse_regs_pkg::LR_STATUS_EV_LO + {}]"
    tx_active = "h_rdata[tse_regs_pkg::LR_STATUS_TX_ACTIVE_LO]"

    def pin(w):
        return f"vout[{low.vpin[w]}]"

    def wire_of(ins):
        """(wire, level) a checker watches at an instant: a side-set wire if it drives one."""
        drives = getattr(ins, "drives", {})
        return next(((w, drives[w]) for w in low.side + [low.codec_pin] if w in drives), None)
    for k, ins in enumerate(prog):
        ob = getattr(ins, "ob", None)
        if ob is None:
            continue
        st, line = stats.get(id(ins)), ob.span.line
        if ob.kind == "feed":
            bit = STATUS_EV.get(ob.mark)
            waits = marked(prog, ob.mark, k, fw=True)
            if bit is None or not waits or st is None or st.vmax in (None, False):
                skipped.append(f"line {line}: {ob.name} (its event is not on STATUS)")
                continue
            d = extra_cycles(ins, lane, fm)
            checks.append(Check("window", line, ob.name, lo=st.vmin + 1 + d, hi=st.vmax + 1 + d,
                                en=f"stay >= 3'd5 && pc_q == 8'd{waits[-1]}", trig=ev.format(bit), tval=1,
                                resp=f"pc_q == 8'd{k} && pc != 8'd{k}", rval=1,
                                what=f"instruction {k}, {d} extra cycles, D in [{st.vmin}, {st.vmax}]"))
        elif ob.kind == "place":
            budget = ob.tol - Fr(ppm, 10**6) * ob.ideal
            to = wire_of(ins)
            if ob.mark == "txend":
                w = marked(prog, "txend", k, fw=True)[-1]
                trig, tval, en = tx_active, 0, f"stay >= 3'd5 && pc_q == 8'd{w}"
            else:
                m = marked(prog, ob.mark)[0]
                fr = wire_of(prog[m])
                trig, tval, en = (None,) * 3
                if fr is not None:
                    trig, tval, en = pin(fr[0]), fr[1], f"(pc == 8'd{m} || pc_q == 8'd{m})"
            if to is None or trig is None:
                skipped.append(f"line {line}: {ob.name} (no wire to watch)")
                continue
            checks.append(Check("window", line, ob.name, lo=math.ceil(ob.ideal - budget),
                                hi=math.floor(ob.ideal + budget), en=en, trig=trig, tval=tval,
                                resp=pin(to[0]), rval=to[1],
                                what=f"pins, {float(ob.ideal):.2f} +/- {float(budget):.2f} ticks"))
        elif ob.kind == "hold":
            skipped.append(f"line {line}: {ob.name} (not checked)")
    return checks, skipped


def frame_harness(role, lane, checks, regs, words, window, host_ready=False):
    """Top module text with one checker per check. With host_ready (the role's labelled
    assumption) the host writes the mailbox every cycle, so a PULL never waits."""
    lines = top_lines(role, lane, regs, words, window, "frame role checkers")
    if host_ready:
        lines += ["  // assumption host_ready: a host word is always waiting",
                  "  always_comb if (!init) assume (h_tx_we);"]
    for k, c in enumerate(checks):
        lines.append(f"  // line {c.line}: {c.name} ({c.what})")
        lines += [f"  tse_window_check #(.LO({c.lo}), .HI({c.hi}), .QUIET(1), "
                  f".W({max(c.hi, 2).bit_length() + 1})) ob{k} ("]
        lines += [f"    .clk, .rst(init), .en({c.en}), .trig_in({c.trig}), .trig_val(1'b{c.tval}),",
                  f"    .resp_out({c.resp}), .resp_val(1'b{c.rval}));"]
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


def frame_sby(role_name):
    """Prove every checker with abc pdr. No cover task: the first frame's events come a few
    hundred cycles after RUN, too deep for a bounded cover; a checker whose window is tightened
    failing (a counterexample) shows its trigger and response are reached."""
    files = [os.path.join(SRC, f) for f in RTL]
    reads = " ".join(os.path.basename(f) for f in files)
    return "\n".join([
        "[options]", "mode prove", "",
        "[engines]", "abc pdr", "",
        "[script]", f"read_verilog -formal -sv {reads} {' '.join(CHECKERS)} top_{role_name}.sv",
        "prep -top top", "flatten", "memory_map", "opt -fast", "techmap", "opt -fast",
        "setundef -undriven -anyseq", "setundef -zero", "opt -full", "opt_dff -sat", "opt_clean", "",
        "[files]", *files, *CHECKERS, f"top_{role_name}.sv",
    ]) + "\n"


def write_frames(outdir, role, lane, checks, regs, words, window, host_ready=False):
    """Write the harness of one frame role."""
    os.makedirs(outdir, exist_ok=True)
    for t in CHECKERS:
        shutil.copy(os.path.join(TEMPLATES, t), outdir)
    name = role.name.text
    with open(os.path.join(outdir, f"top_{name}.sv"), "w") as fh:
        fh.write(frame_harness(role, lane, checks, regs, words, window, host_ready))
    with open(os.path.join(outdir, f"{name}.sby"), "w") as fh:
        fh.write(frame_sby(name))
    with open(os.path.join(outdir, f"{name}.hex"), "w") as fh:
        fh.write("".join(f"{w:04x}\n" for w in words))
