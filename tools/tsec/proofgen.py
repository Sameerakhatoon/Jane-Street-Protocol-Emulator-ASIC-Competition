# SPDX-License-Identifier: Apache-2.0
"""Stage 7, proof generation: a SymbiYosys harness per compiled role, on the real lane RTL.

The harness top instantiates src/tse_lane.sv with what the chip gives it:
its configuration bank (src/tse_cfg_bank.sv, starting from the image's lane
registers, so the program's own CSRW to a bank word takes effect; a program
without CSRW gets the image as a constant, and an assertion that it never
writes), a
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
from ..tseasm import isa
from .semantics import base_event, min_separation, opposite

TEMPLATES = os.path.join(os.path.dirname(__file__), "proofs", "templates")
SRC = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
RTL = ["tse_isa_pkg.sv", "tse_regs_pkg.sv", "tse_seq.sv", "tse_dpll.sv", "tse_crc.sv",
       "tse_resp_rule.sv", "tse_stuffer.sv", "tse_codec.sv", "tse_nco.sv", "tse_timer.sv",
       "tse_lane.sv", "tse_cfg_bank.sv"]
BASE = {"out": 3, "in": 0}
SYNC = 2                        # the harness's two-flop synchronizer on the pins


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
        self.path = None            # pcs from the waits to act; leaving them abandons the check


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
        direct, path = act_path(att.prog, att.labels, waits, k)
        if not waits:
            continue
        waits = direct or waits     # without a direct path the check stays as strict as before
        sep, _ = min_separation(sym, role, opposite(base), base, fclk)
        out.append(Ob(f"{ob.name} after {base.kind}({wire})", ob.span.line, ob.kind, ob.lo, ob.hi, k,
                      extra_cycles(ins, lane, fm), att.fsm.vin(wire), val,
                      waits, max(1, math.floor(sep))))
        out[-1].path = path
    return out


def cover_depth(prog, labels, lane, fm, obs):
    """Cycles from RUN within which every checker can first respond: the shortest run to each
    acting instruction, counted loops run their full count. A fixed depth is too shallow for a
    protocol whose first window comes after many bit times (I2C: a START and eight address
    bits). The harness pins are free, so an edge wait costs only the synchronizer, except at a
    wait some checker is armed at: its trigger needs QUIET stable cycles first (the checker's
    own assumption). Every instruction costs a dispatch plus a slot frame of fetch stall."""
    import heapq
    quiet = {}
    for ob in obs:
        for p in ob.waits:
            quiet[p] = max(quiet.get(p, 0), ob.quiet)

    def cost(pc):
        ins = prog[pc]
        c = 1 + fm.S
        if ins.op == "WAIT" and ins.wait is not None and ins.wait.kind in ("edge", "pre"):
            c += max(SYNC, quiet.get(pc, 0))
        return c

    def succ(pc, x, y):
        ins, a = prog[pc], prog[pc].a
        if ins.op == "JMP":
            tgt, c = labels[a["target"]], a["cond"]
            if c == "ALWAYS":
                return [(tgt, x, y)]
            if c in ("XDEC", "YDEC"):
                v = x if c == "XDEC" else y
                if v is None:           # a count from data: any number of passes
                    return [(tgt, x, y), (pc + 1, x, y)]
                n = (v - 1) & 0xFFFF
                nxt = tgt if v != 0 else pc + 1
                return [(nxt, n, y) if c == "XDEC" else (nxt, x, n)]
            return [(tgt, x, y), (pc + 1, x, y)]
        if ins.op == "SET" and a.get("dst") in ("X", "Y"):
            return [(pc + 1, a["imm"], y) if a["dst"] == "X" else (pc + 1, x, a["imm"])]
        if (ins.op in ("OUT", "MOV") and a.get("dst") in ("X", "Y")) or \
                (ins.op == "SET" and a.get("dst") == "XH"):
            return [(pc + 1, None, y) if a["dst"] in ("X", "XH") else (pc + 1, x, None)]
        if ins.op == "CSRR":
            return [(pc + 1, None, None)]
        return [(pc + 1, x, y)]
    start = (0, None, None)
    dist, best, todo = {start: cycles.start_cycle(lane, fm)}, {}, [(cycles.start_cycle(lane, fm), start)]
    while todo:
        d, st = heapq.heappop(todo)
        if d > dist[st]:
            continue
        pc = st[0]
        best[pc] = min(best.get(pc, d), d)
        for n in succ(*st):
            if n[0] >= len(prog):
                continue
            nd = d + cost(pc)
            if nd < dist.get(n, nd + 1):
                dist[n] = nd
                heapq.heappush(todo, (nd, n))
    reach = [best[ob.act] + math.ceil(ob.hi) + 2 for ob in obs if ob.act in best]
    return max([40] + [r + 30 for r in reach])


def abandon(ob):
    """The checker's abandon input: the lane is off every path from the waits to the act."""
    if ob.path is None:
        return "1'b0"
    return "!(" + " || ".join(f"pc_q == 8'd{p}" for p in sorted(ob.path)) + ")"


def act_path(prog, labels, waits, act):
    """(the waits act follows directly, the pcs from them to act): paths that wait for nothing
    else on the way, except that a pre-wait passes on to the edge wait it prepares. A trigger
    seen at any other wait is answered later, after another one; leaving the path means a
    guard chose another branch or an abort came. (None, None) when no wait leads to act."""
    def succ(pc):
        ins = prog[pc]
        out = [] if ins.op == "JMP" and ins.a["cond"] == "ALWAYS" else [pc + 1]
        if ins.op == "JMP":
            out.append(labels[ins.a["target"]])
        return [p for p in out if p < len(prog)]

    def blocks(n):
        w = prog[n].wait
        return (w is not None and w.kind in ("edge", "pre", "pull")) or \
            (prog[n].op == "PULL" and prog[n].a.get("blk"))
    direct, path = [], set()
    for w0 in waits:
        pre = prog[w0].wait.kind == "pre"
        start = (w0, not pre)               # (pc, the edge wait is behind us)
        seen, todo = {start}, [start]
        while todo:
            p, done = todo.pop()
            if p == act:
                continue
            for n in succ(p):
                nxt = (n, done)
                if n != act and blocks(n):
                    if done or n not in waits or prog[n].wait.kind != "edge":
                        continue
                    nxt = (n, True)
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        if not any(p == act for p, _ in seen):
            continue
        back, changed = {(p, d) for p, d in seen if p == act}, True
        while changed:
            changed = False
            for s in seen - back:
                p, d = s
                nd = [(n, d) for n in succ(p)] + [(n, True) for n in succ(p)]
                if any(x in back for x in nd):
                    back.add(s)
                    changed = True
        direct.append(w0)
        path |= {p for p, _ in back}
    return (direct, path) if direct else (None, None)


def only(k, en):
    """Checker k's enable under the top's ONLY parameter. A checker that never arms has no
    assumption and no assertion left, so proving one obligation alone drops the others'
    environment assumptions: a weaker hypothesis, so a pass is at least as strong."""
    return f"(ONLY < 0 || ONLY == {k}) && {en}"


def late(k, lo, hi):
    """Checker k's least latency under the top's LATE parameter: with LATE == k every response
    comes too early (one past the deadline), so task late<k> must FAIL. That is the planted bug
    of the proof of obligation k, and its counterexample shows trigger and response reached."""
    return f"LATE == {k} ? {hi + 1} : {lo}"


def ob_tasks(n, group=None):
    """sby task lines: per obligation a proof (ob<k>) and its planted bug (late<k>), below the
    "--" line so a plain run skips them."""
    return ["--"] + [f"{t}{k}" + (f" {group}" if group else "") for k in range(n) for t in ("ob", "late")]


def ob_params(n):
    return [f"ob{k}: chparam -set ONLY {k} top" for k in range(n)] + \
        [f"late{k}: chparam -set ONLY {k} -set LATE {k} top" for k in range(n)]


def harness(role, lane, obs, regs, words, window, pads):
    """(top module text, [(Ob, LO, HI)])."""
    rows = [(ob, *window_bounds(ob.lo, ob.hi, pads[0], pads[1], ob.kind, ob.extra)) for ob in obs]
    lines = top_lines(role, lane, regs, words, window, "one checker per OB-WIN obligation")
    for k, (ob, lo, hi) in enumerate(rows):
        en = only(k, "stay >= 3'd5 && (" + " || ".join(f"pc_q == 8'd{p}" for p in ob.waits) + ")")
        lines += [
            f"  // line {ob.line}: {ob.name} within [{float(ob.lo):.2f}, {float(ob.hi):.2f}] ticks "
            f"(instruction {ob.act}, {ob.extra} extra cycles)",
            f"  tse_window_check #(.LO({late(k, lo, hi)}), .HI({hi}), .QUIET({ob.quiet}), "
            f".W({max(hi, ob.quiet, 2).bit_length() + 1})) ob{k} (",
            f"    .clk, .rst(init), .en({en}), .trig_in(sync[{ob.pin}]), .trig_val(1'b{ob.val}),",
            f"    .resp_out(pc_q == 8'd{ob.act} && pc != 8'd{ob.act}), .resp_val(1'b1),",
            f"    .abandon({abandon(ob)}));",
        ]
    lines.append("endmodule")
    return "\n".join(lines) + "\n", rows


def writes_bank(words):
    """True if the program has a CSRW, the one instruction that writes the configuration bank."""
    return any((ins := isa.decode(w)) is not None and ins.name == "CSRW" for w in words)


def top_lines(role, lane, regs, words, window, what):
    """The harness top up to its checkers: lane, configuration, program store, data window, the
    STATUS read port (pc, pc_q, stay)."""
    pc_hi, pc_lo = "tse_regs_pkg::LR_STATUS_PC_HI", "tse_regs_pkg::LR_STATUS_PC_LO"
    lines = [
        f"// top_{role.name.text}.sv: generated by tsec, do not edit.",
        f"// Role {role.name.text} on lane {lane}, the real tse_lane; {what}.",
        "// ONLY >= 0 arms checker ONLY alone (a smaller proof per obligation); -1 arms them all.",
        "// LATE = k plants a bug in checker k: every response is too early for it.",
        "module top #(parameter int ONLY = -1, parameter int LATE = -1)",
        "           (input logic clk, input logic [3:0] pin_in,",
        "            input logic h_tx_we, input logic [31:0] h_tx_data, input logic h_rx_re);",
        "  logic init = 1'b1;",
        "  always_ff @(posedge clk) init <= 1'b0;",
        "  logic rst_n, run;",
        "  assign rst_n = !init;",
        "  assign run = !init;",
        "  logic [3:0] s1, sync;                    // two-flop synchronizer, as in the chip",
        "  always_ff @(posedge clk) begin s1 <= pin_in; sync <= s1; end",
        "  logic [3:0] cnt = 4'd0;                  // the 16-cycle slot frame from RUN",
        "  always_ff @(posedge clk) cnt <= run ? cnt + 4'd1 : 4'd0;",
        "  logic [1023:0] cfg, cfg_img;             // the bank, which starts as the image loads it",
        "  always_comb begin",
        "    cfg_img = '0;",
    ]
    for name, value in sorted(regs.items()):
        lines.append(f"    cfg_img[32*tse_regs_pkg::LW_{name} +: 32] = 32'h{value:08X};")
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
    ]
    if writes_bank(words):
        lines += [
            "  // the lane's configuration bank as in tse_top, so the program's CSRW to a bank word",
            "  // takes effect; flops (the chip's latches show a new value one edge sooner)",
            "  logic cfg_busy;",
            "  logic [31:0] cfg_rd;",
            "  tse_cfg_bank #(.WORDS(32), .LATCH(0), .WD_REG(0), .MASK(tse_regs_pkg::LANE_BANK_MASK)) bank (",
            "    .clk, .rst_n, .clr(1'b0), .we(cfg_we), .addr(cfg_word), .wdata(cfg_wdata), .busy(cfg_busy),",
            "    .rd_addr(5'd0), .rd_data(cfg_rd), .q(cfg));",
            "  always_comb if (init) assume (cfg == (cfg_img & tse_regs_pkg::LANE_BANK_MASK));",
        ]
    else:
        lines += [
            "  // the program has no CSRW, so the bank keeps the image: a constant, which keeps the",
            "  // proof small; the assertion checks that the lane never writes it",
            "  assign cfg = cfg_img & tse_regs_pkg::LANE_BANK_MASK;",
            "  always_comb if (!init) assert (!cfg_we);",
        ]
    lines += [
        "  logic [7:0] pc, pc_q;",
        f"  assign pc = h_rdata[{pc_hi}:{pc_lo}];",
        "  always_ff @(posedge clk) pc_q <= pc;",
        "  logic [2:0] stay = 3'd0;                 // cycles the lane has been at pc_q",
        "  always_ff @(posedge clk) stay <= pc != pc_q ? 3'd0 : stay == 3'd7 ? stay : stay + 3'd1;",
    ]
    return lines


def sby_text(role_name, depth=40, bounded=False, n=0):
    """Prove with abc pdr, or (bounded) check every run of depth cycles from RUN with smtbmc:
    a paced frame's long delay loops keep pdr from converging in reasonable time. Tasks ob0 to
    ob<n-1> prove one obligation each, with rIC3's portfolio as for a frame role (the I2C
    target's single obligations closed with it in 7 to 27 minutes, and not in an hour with abc
    pdr); bounded, the same way as the whole role."""
    files = [os.path.join(SRC, f) for f in RTL]
    reads = " ".join(os.path.basename(f) for f in files)
    mode = ["mode bmc", f"depth {depth}"] if bounded else ["mode prove"]
    return "\n".join([
        "[tasks]", "prove prv", "cover cvr", *ob_tasks(n, "obp"), "",
        "[options]", *(f"prv: {m}" for m in mode), *(f"obp: {m}" for m in mode),
        "cvr: mode cover", f"cvr: depth {depth}", "",
        "[engines]", "prv: " + ("smtbmc boolector" if bounded else "abc pdr"),
        "obp: " + ("smtbmc boolector" if bounded else "aiger rIC3"), "cvr: smtbmc boolector", "",
        "[script]", f"read_verilog -formal -sv {reads} tse_window_check.sv top_{role_name}.sv",
        *ob_params(n), "prep -top top", "flatten", "memory_map", "opt -fast", "techmap", "opt -fast",
        "setundef -undriven -anyseq", "setundef -zero", "opt -full", "opt_dff -sat", "opt_clean", "",
        "[files]", *files, "tse_window_check.sv", f"top_{role_name}.sv",
    ]) + "\n"


def write(outdir, role, lane, obs, regs, words, window, pads, bounded=False, depth=40):
    """Write the harness of one role; returns the checker rows (empty: nothing observable).
    depth: the cover depth (cover_depth for a state machine)."""
    os.makedirs(outdir, exist_ok=True)
    shutil.copy(os.path.join(TEMPLATES, "tse_window_check.sv"), outdir)
    top, rows = harness(role, lane, obs, regs, words, window, pads)
    name = role.name.text
    with open(os.path.join(outdir, f"top_{name}.sv"), "w") as fh:
        fh.write(top)
    with open(os.path.join(outdir, f"{name}.sby"), "w") as fh:
        if bounded:
            depth = max(depth, max(hi for _, _, hi in rows) + 30)
        fh.write(sby_text(name, depth, bounded, len(rows)))
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
    assumption) the host writes the mailbox every cycle, so a PULL never waits. Every trigger
    is the lane's own (an engine flag, TX_ACTIVE, its own pin), so the window checkers assume
    nothing about it (ENV 0): a response that clears its trigger must not prune the trace."""
    lines = top_lines(role, lane, regs, words, window, "frame role checkers")
    if host_ready:
        lines += ["  // assumption host_ready: a host word is always waiting",
                  "  always_comb if (!init) assume (h_tx_we);"]
    for k, c in enumerate(checks):
        lines.append(f"  // line {c.line}: {c.name} ({c.what})")
        lines += [f"  tse_window_check #(.LO({late(k, c.lo, c.hi)}), .HI({c.hi}), .QUIET(1), "
                  f".W({max(c.hi, 2).bit_length() + 1}), .ENV(0)) ob{k} ("]
        lines += [f"    .clk, .rst(init), .en({only(k, '(' + c.en + ')')}), .trig_in({c.trig}),"
                  f" .trig_val(1'b{c.tval}),",
                  f"    .resp_out({c.resp}), .resp_val(1'b{c.rval})"
                  ", .abandon(1'b0));"]
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


def frame_sby(role_name, n=0):
    """Prove every checker (task prove), or one checker alone (tasks ob0 to ob<n-1>), with
    rIC3's portfolio: abc pdr did not converge in 50 minutes on any of them, all together or
    alone. No cover task: the first frame's events come a few hundred cycles after RUN, too deep
    for a bounded cover; instead task late<k>, checker k with a window no response can meet,
    must fail, which shows its trigger and response are reached."""
    files = [os.path.join(SRC, f) for f in RTL]
    reads = " ".join(os.path.basename(f) for f in files)
    return "\n".join([
        "[tasks]", "prove", *ob_tasks(n), "",
        "[options]", "mode prove", "",
        "[engines]", "aiger rIC3", "",
        "[script]", f"read_verilog -formal -sv {reads} {' '.join(CHECKERS)} top_{role_name}.sv",
        *ob_params(n), "prep -top top", "flatten", "memory_map", "opt -fast", "techmap", "opt -fast",
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
        fh.write(frame_sby(name, len(checks)))
    with open(os.path.join(outdir, f"{name}.hex"), "w") as fh:
        fh.write("".join(f"{w:04x}\n" for w in words))
