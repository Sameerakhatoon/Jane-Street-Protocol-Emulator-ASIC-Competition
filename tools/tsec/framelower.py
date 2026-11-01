"""Stage 4c, lowering of frame roles: `send`, `recv`, response rules and assist events.

A frame role hands the bit work to the lane's engines (TX and RX shift
registers, codec, stuffer, CRC, DPLL, response rules) and keeps the
sequencer for what they cannot do: feeding and draining the 32-bit shift
registers, frame resets, the per-frame configuration writes, tests of the
end-of-frame status and the host words. Every instruction whose timing
matters carries an obligation (Ob) the timing analysis (frametime.py) proves:

  feed     an event-serving instruction within its deadline after the event is
           seen (TXNEED to the TXDATA write, RXFULL to the RXDATA read, ...)
  place    an output instant at its ideal distance from an earlier one (`for T ~ tol`)
  hold     a least distance to the next state (`for T` on the last statement)
  rearm    the start-of-frame detection armed before the next frame can start

Marks name the cycles obligations and event constraints are measured from:
an instruction sets one when it dispatches, a WAIT sets one at the cycle its
event became visible.

Shapes (anything else is R300 with the reason):
  idle states   `on host.pull(v)`, one assist event, both (the event's WAIT carries
                the "host word waiting" alternate), or one edge of the DPLL's pin
                in front of a `recv` (the DPLL's hard sync takes the edge)
  seq states    drives (instants, `for`, `~`), `send`, `recv`, `repeat N { await E; }`,
                `push host`, `emit`, `abort on host.pull(v)`
  status states after a `recv`: edges on the frame's CRC result or end of frame,
                and a `respond` that answers in the frame's own `from ROLE` field
"""
from dataclasses import dataclass, field
from fractions import Fraction as Fr

from . import assist_events as AE
from . import ast as A
from . import frames, printer
from ..tseasm import regs
from ..tseasm.isa_table import TABLE
from .assists import Params
from .diag import TseError
from .lir import Ins
from .semantics import min_separation

WIDX = TABLE["wait_idx"]
CMD = {k: 1 << v["lo"] for k, v in regs.LANE["CMD"]["fields"].items()}
REG = {k: v["addr"] for k, v in regs.LANE.items()}
DATA_BASE = 32                  # data window words per lane for buffered frames


@dataclass
class Ob:
    kind: str                   # feed, place, hold, rearm
    code: str                   # R316, R310, R313, R314, R311
    name: str
    span: object
    mark: str
    lo: object = None           # cycles (dispatch - mark + offset)
    hi: object = None
    offset: int = 0
    ideal: object = None
    why: str = ""
    fix: str = ""


@dataclass
class FWait:
    key: str                    # TXNEED, RXFULL, RULEHIT, ERR, TIMER, TXIDLE, BITSTART, HOST
    mark: str = None            # set to the cycle the event became visible
    earliest: tuple = None      # (mark, cycles): not visible before
    alt: str = None
    alt_mark: str = None
    alt_earliest: tuple = None
    pulse: bool = False         # visible only in its own cycle
    test: bool = False          # WAIT with timeout 0: a level test, either outcome


@dataclass
class Rule:
    match: int = 0
    resp: int = 0
    pos: int = 0
    any: int = 0
    rlen: int = 1
    offset: int = 0
    cond: str = "NONE"
    drive: int = 1


@dataclass
class Status:
    good: str                   # labels the status state binds
    bad: str
    ack: object = None          # respond answering in the frame's own field
    stream: bool = False        # the frame went to the host while it arrived
    frame: object = None


class FrameLowering:
    def __init__(self, sym, role, fclk, lane=0):
        self.sym, self.role, self.f, self.lane = sym, role, Fr(fclk), lane
        self.states = {s.name.text: s for s in role.states}
        self.init = next((s for s in role.states if s.initial), role.states[0])
        self.prog, self.nlabel, self.done, self.queue = [], 0, set(), []
        self.cfg, self.rules, self.notes, self.warnings = {}, [], {}, []
        self.emit_codes, self.status_for, self.vars = {}, {}, {v.name.text: v for v in role.vars}
        self.crc_prefix, self.crc_extra, self.dpll_from_unit = [], [], False
        self.side_level, self.entry_level, self.alt = {}, {}, None
        self.timer_reload, self.windows = None, []
        self.host_ready = any(a.a.kind == "named" and a.a.name.text == "host" and a.a.attr is not None
                              and a.a.attr.text == "ready" for a in role.assumes)
        AE.check_resources(sym, role)
        self.assists = {a.kind: a for a in sym.assists.values()}
        self.plan_pins()
        self.plan_clock()

    # ------------------------------------------------------------ resources
    def err(self, msg, span, code="R300"):
        return TseError(code, f"not supported by this version of tsec: {msg}" if code == "R300" else msg,
                        span)

    def plan_pins(self):
        uses = [u.text for u in self.role.uses]
        wires = self.sym.wires
        codec = self.assists.get("manchester") or self.assists.get("nrzi")
        self.codec_pin = Params(self.sym, codec).pins("pins")[0] if codec else None
        outs = [w for w in uses if wires[w].dir in ("out", "inout")]
        ins = [w for w in uses if wires[w].dir in ("in", "inout")]
        self.rule_pin = self.find_rule_pin(outs)
        tx = self.codec_pin if self.codec_pin in outs else self.rule_pin
        rx = self.codec_pin if self.codec_pin in ins else (ins[0] if ins else None)
        order = [w for w in (tx, rx) if w is not None]
        self.side = [w for w in outs if w not in order]
        if len(self.side) > 2:
            raise self.err("more than two wires driven by the program beside the engines",
                           self.role.name.span, "R307")
        order += self.side + [w for w in uses if w not in order and w not in self.side]
        if len(order) > 4:
            raise self.err(f"{len(order)} wires, a lane has 4 virtual pins", self.role.name.span, "R307")
        self.vpin = {w: k for k, w in enumerate(order)}
        self.tx_pin, self.rx_pin = tx, rx
        if tx is not None:
            self.cfg["txpin"] = self.vpin[tx]
        if rx is not None:
            self.cfg["rxpin"] = self.vpin[rx]
        if self.codec_pin is not None and self.codec_pin == tx:
            self.cfg["tx_owns_pin"] = 1
        if self.side:
            self.cfg.update(side_en=1, ss_base=self.vpin[self.side[0]])
            self.side_level = {w: self.idle_of(w) for w in self.side}

    def find_rule_pin(self, outs):
        found = []

        def fn(x):
            if isinstance(x, A.RespondEdge):
                for s in x.body:
                    if isinstance(s, A.Drive):
                        found.append(s.wire.text)
        AE.walk(self.role.states, fn)
        return found[0] if found and found[0] in outs else None

    def idle_of(self, w):
        at = self.sym.wires[w].attr("idle")
        return int(self.sym.eval(at.value).v) if at is not None else 0

    def plan_clock(self):
        """The bit period: the DPLL's, or for a sender without one the unit `bit` (the DPLL then
        runs as a bare bit timer)."""
        d = self.assists.get("dpll")
        if d is not None:
            p = Params(self.sym, d, self.f)
            self.period = p.ticks("period")
            self.sjw = p.ticks("sjw", lo=0)
            self.sample = p.number("sample", lo=0, hi=1, whole=False)
        else:
            self.period, self.sjw, self.sample = None, Fr(0), Fr(1, 2)
            if "bit" in self.sym.units:
                self.period = self.sym.units["bit"].v * self.f
                self.dpll_from_unit = True
        if self.period is not None:
            q16 = round(self.period * 16)
            self.q16 = q16
            self.p_min = (q16 - round(self.sjw * 16)) // 16    # a resync may shorten a bit by sjw

    # ------------------------------------------------------------ emission
    def label(self, stem="L"):
        self.nlabel += 1
        return f"{stem}{self.nlabel}"

    def place(self, name, align=False):
        ins = Ins("LABEL", {}, label=name)
        ins.align = align
        self.prog.append(ins)

    def emit(self, op, note="", **a):
        ins = Ins(op, a, note=note)
        if self.side:
            ss = 0
            for k, w in enumerate(self.side):
                ss |= self.side_level[w] << k
            ins.a["ss"] = ss
        self.prog.append(ins)
        return ins

    def wait(self, src, idx, pol=1, alt=False, to=False, note=""):
        a = {"pol": pol, "src": src, "idx": WIDX[src][idx] if isinstance(idx, str) else idx}
        if alt:
            a["alt"] = 1
        if to:
            a["to"] = 1
        return self.emit("WAIT", note=note, **a)

    def cmd(self, *names, note=""):
        v = 0
        for n in names:
            v |= CMD[n]
        return self.emit("SET", dst="CMD", imm=v, note=note)

    def load_yx(self, v):
        hi, lo = v >> 16, v & 0xFFFF
        if hi > 0xFF:
            self.emit("SET", dst="X", imm=hi & 0xFF)
            self.emit("SET", dst="XH", imm=hi >> 8)
            self.emit("MOV", dst="Y", src="X")
        else:
            self.emit("SET", dst="Y", imm=hi)
        self.emit("SET", dst="X", imm=lo & 0xFF)
        if lo >> 8:
            self.emit("SET", dst="XH", imm=lo >> 8)

    def csrw(self, name, note=""):
        return self.emit("CSRW", addr=REG[name], note=note or f"write {name}")

    def goto(self, state):
        if state not in self.states:
            raise TseError("R300", f"no state '{state}'", self.role.name.span)
        lvl = dict(self.side_level)
        if state in self.entry_level and self.entry_level[state] != lvl:
            raise self.err(f"state '{state}' is entered with different levels on "
                           f"{', '.join(self.side)}", self.states[state].name.span)
        self.entry_level[state] = lvl
        self.entry_pulled.setdefault(state, set()).add(self.pulled)
        self.jump(state)
        if state not in self.done:
            self.done.add(state)
            self.queue.append(state)

    # ------------------------------------------------------------ driver
    def lower(self):
        self.prologue()
        self.entry_pulled = {self.init.name.text: {None}}
        self.resp_sets = []
        self.done.add(self.init.name.text)
        self.entry_level[self.init.name.text] = dict(self.side_level)
        self.queue.append(self.init.name.text)
        used = {}
        while self.queue:
            name = self.queue.pop(0)
            self.side_level = dict(self.entry_level[name])
            got = self.entry_pulled.get(name, {None})
            self.pulled = next(iter(got)) if len(got) == 1 else None
            self.place(name)
            self.state(self.states[name])
            if self.pulled_used:
                used[name], self.pulled_used = self.pulled_used, None
        for name, var in used.items():          # entries found after the state was lowered
            if self.entry_pulled[name] != {var}:
                raise self.err(f"state '{name}' sends '{var}', but a way into it does not pull '{var}' "
                               "from the host just before", self.states[name].name.span)
        for ins, span in self.resp_sets:        # the rule position write keeps the answer bits
            resp = self.rules[0].resp
            if resp > 0xFF:
                raise self.err("an answer of more than 8 bits written with the rule position", span)
            ins.a["imm"] = resp
        return self

    pulled_used = None

    def prologue(self):
        dirs = sum(1 << self.vpin[w] for w in self.side)
        if self.rule_pin is not None and self.rule_pin != self.codec_pin:
            v = self.idle_of(self.rule_pin)
            k = self.vpin[self.rule_pin]
            self.emit("SET", dst="PINS", imm=(1 << k) << 4 | v << k, note=f"{self.rule_pin} idle")
            dirs |= 1 << k
        if dirs:
            self.emit("SET", dst="PINDIRS", imm=dirs << 4 | dirs, note="outputs")
        if self.sends():
            self.emit("SET", dst="X", imm=31)
            self.csrw("TXCTL", "32 bits per TXDATA word")
        if self.period is not None:
            self.cmd("dpll_on", note="bit timer on")

    def sends(self):
        hit = []
        AE.walk(self.role.states, lambda x: hit.append(x) if isinstance(x, A.Send) else None)
        return bool(hit)

    # ------------------------------------------------------------ states
    def state(self, st):
        name = st.name.text
        if name in self.status_for:
            return self.status_state(st, self.status_for[name])
        edges = st.edges
        if len(edges) == 1 and isinstance(edges[0], A.SeqEdge):
            return self.seq_state(st, edges[0])
        if all(isinstance(e, A.OnEdge) and e.guard is None and not e.body for e in edges):
            return self.on_state(st, edges)
        raise self.err(f"the edges of state '{name}' (a frame role lowers `on` edges, one `seq`, "
                       "or the edges after a `recv`)", st.name.span)

    def on_state(self, st, edges):
        host = [e for e in edges if is_host_pull(e.event)]
        evs = [e for e in edges if AE.named(e.event) and e.event.name.text in self.sym.assists]
        pins = [e for e in edges if e.event.kind in ("rise", "fall", "edge")]
        if len(host) + len(evs) + len(pins) != len(edges) or len(host) > 1 or len(evs) > 1 \
                or (pins and (host or evs or len(pins) > 1)):
            raise self.err(f"this combination of events in state '{st.name.text}'", st.name.span)
        if pins:
            return self.arm_edge(st, pins[0])
        if evs:
            e = evs[0]
            a, sig = AE.lookup(self.sym, e.event)
            pull = self.label("pull") if host else None
            self.timer_event0(e.event)
            self.await_event(e.event, a, sig, abort=pull, entry=True)
            self.goto(e.target.text)
            if host:
                self.place(pull)
                self.host_pull(host[0])
            return
        self.host_pull(host[0])

    def host_pull(self, e):
        ins = self.emit("PULL", blk=1, note=f"pull host {printer.event(e.event)}")
        ins.fw = FWait("HOST")
        v = e.event.args[0].name.text if e.event.args else None
        self.pulled = v
        self.goto(e.target.text)

    def arm_edge(self, st, e):
        tgt = self.states[e.target.text]
        body = tgt.edges[0].body if len(tgt.edges) == 1 and isinstance(tgt.edges[0], A.SeqEdge) else []
        w = e.event.name.text
        if not (body and isinstance(body[0], A.Recv)) or w != self.rx_pin or "dpll" not in self.assists:
            raise self.err(f"waiting for {printer.event(e.event)} in a frame role: only the edge that "
                           "starts a `recv` is supported, taken by the DPLL's hard sync on its pin",
                           e.event.span)
        edges = Params(self.sym, self.assists["dpll"]).word("edges", ("rise", "fall", "both"))
        if e.event.kind != "edge" and edges not in (e.event.kind, "both"):
            raise self.err(f"the DPLL hard-syncs on {edges} edges but the state waits for "
                           f"{printer.event(e.event)}", e.event.span)
        if e.event.kind == "edge" and edges != "both" or e.event.kind != "edge" and edges == "both":
            raise TseError("R300", f"the DPLL's edges ({edges}) differ from {printer.event(e.event)}: "
                           "state the same edges in the assist", e.event.span)
        ins = self.cmd("frame_rst", "crc_clear", "hard_arm",
                       note=f"arm: the DPLL takes {printer.event(e.event)} as the frame start")
        ins.mark = "arm"
        if self.rearm is not None:
            ins.ob = self.rearm
        if any(f.count for f in frames.layout(self.sym, body[0].name.text, body[0].span).fields):
            self.emit("CSRR", addr=REG["RXDATA"], note="drop a word the idle line filled: RXFULL off")
        self.goto(e.target.text)

    rearm = None
    pulled = None

    def await_event(self, ev, a, sig, abort=None, entry=False):
        """WAIT for an assist event; a timer tick is counted from a restart just before it."""
        fw = FWait(sig.idx)
        if a.kind == "timer":
            if self.uses_recv():
                raise self.err("a lane timer in a receiving role: restarting it needs a frame reset, "
                               "which would also reset the receiver", ev.span)
            r = self.cmd("frame_rst", note=f"restart the timer for {printer.event(ev)}")
            r.mark = "tmr"
            fw.earliest = ("tmr", self.timer_ticks(a))
        if abort is not None:
            self.set_alt((1, "FLAG", WIDX["FLAG"]["HTXF"]), ev.span)
            fw.alt = "HOST"
        ins = self.wait(sig.src, sig.idx, sig.pol, alt=abort is not None, note=f"await {printer.event(ev)}")
        ins.fw = fw
        if abort is not None:
            self.jump(abort, "TOUT")

    def timer_event0(self, ev):
        """Event 0 is TXNEED while a frame goes out and the timer otherwise."""
        a = self.sym.assists.get(ev.name.text) if AE.named(ev) else None
        if a is not None and a.kind == "timer" and self.sends():
            self.load_yx(self.timer_ctl(a, 1))
            self.csrw("TIMER_CTL", "event 0 is the timer")

    def timer_ctl(self, a, ev0):
        from .assists import timer_regs
        v = timer_regs(Params(self.sym, a, self.f))[0]["TIMER_CTL"]
        f = regs.LANE["TIMER_CTL"]["fields"]["ev0_sel"]["lo"]
        return (v & ~(1 << f)) | ev0 << f

    def timer_ticks(self, a):
        t = Params(self.sym, a, self.f).ticks("period", lo=1, hi=Fr(65536))
        self.timer_reload = t
        return int(t)

    def uses_recv(self):
        hit = []
        AE.walk(self.role.states, lambda x: hit.append(x) if isinstance(x, A.Recv) else None)
        return bool(hit)

    def set_alt(self, alt, span):
        if self.alt is not None and self.alt != alt:
            raise self.err("two different WAIT alternates in one role", span)
        self.alt = alt
        self.cfg.update(alt_pol=alt[0], alt_src=alt[1], alt_idx=alt[2])

    # ------------------------------------------------------------ seq states
    def seq_state(self, st, e):
        body = e.body
        abort = None
        if e.abort_on is not None:
            if not is_host_pull(e.abort_on):
                raise self.err(f"abort on {printer.event(e.abort_on)} in a frame role", e.abort_on.span)
            abort = self.label("abort")
        if any(isinstance(s, A.Recv) for s in body):
            if len(body) != 1:
                raise self.err("a `recv` with other statements in its seq", body[0].span)
            self.recv(body[0], e.target.text)
            return
        self.stmts(body, abort, e)
        self.goto(e.target.text)
        if abort is not None:
            self.place(abort)
            self.host_pull(A.OnEdge(e.abort_on, target=e.abort_to))

    def stmts(self, body, abort, edge):
        instants, cur = [], []
        for s in body:
            if isinstance(s, A.Drive):
                cur.append(s)
                if s.timing.for_ is not None:
                    instants.append(("drive", cur))
                    cur = []
                continue
            if cur:
                instants.append(("drive", cur))
                cur = []
            instants.append((type(s).__name__, s))
        if cur:
            instants.append(("drive", cur))
        prev = None             # (mark, offset, gap ticks, tol ticks, span) of the last timed instant
        for k, (kind, s) in enumerate(instants):
            nxt = instants[k + 1][0] if k + 1 < len(instants) else None
            if kind == "drive":
                prev = self.instant(s, prev, after_send=k > 0 and instants[k - 1][0] == "Send",
                                    before_send=nxt == "Send")
                continue
            if prev is not None and prev[2] is not None:
                raise self.err("a timed drive followed by a statement other than a drive",
                               s.span)
            prev = None
            if kind == "Send":
                self.send(s)
            elif kind == "Repeat":
                self.repeat_await(s, abort)
            elif kind == "HostIO":
                self.push(s)
            elif kind == "Emit":
                code = self.emit_codes.setdefault(s.name.text, len(self.emit_codes))
                self.emit("EMIT", code=code, note=f"emit {s.name.text}")
            else:
                raise self.err(f"`{printer.stmt(s, 0).strip()}` in a frame role", s.span)
        if prev is not None and prev[2] is not None:
            self.hold(prev)

    def tol_of(self, group, prev_tol, gap):
        for s in group:
            if s.timing.tol is not None:
                q = self.sym.eval(s.timing.tol)
                return q.v * gap if q.dim == 0 else q.v * self.f
        return prev_tol

    def instant(self, group, prev, after_send=False, before_send=False):
        """One output instant: side-set levels, a codec line level, program pins; all in one
        instruction. Placed at its distance from the previous timed instant."""
        last = group[-1]
        gap = self.sym.eval(last.timing.for_).v * self.f if last.timing.for_ is not None else None
        codec = [s for s in group if s.wire.text == self.codec_pin]
        other = [s for s in group if s.wire.text not in self.side and s.wire.text != self.codec_pin]
        if other:
            raise self.err(f"driving {other[0].wire.text} from the program in a frame role (it is "
                           "neither the codec's pin nor a side-set wire)", other[0].span)
        for s in group:
            if s.wire.text in self.side:
                self.side_level[s.wire.text] = int(self.sym.eval(s.value).v)
        if after_send and codec:
            lvl = int(self.sym.eval(codec[0].value).v)
            if len(group) > 1:
                raise self.err("other drives at the instant a frame ends", group[1].span)
            self.cfg["idle"] = lvl
            mark, off = "txend", AE.TXIDLE_TO_PIN
            return mark, off, gap, self.tol_of(group, None, gap), last.span
        if before_send:
            if codec or gap is not None:
                raise self.err("a timed drive or a drive of the codec's pin before `send`", group[0].span)
            # After a frame the line keeps the level its end left (TP_IDL high, say), so the drives
            # that start a frame also put the line at its wire's idle level until the first bit.
            v = self.idle_of(self.codec_pin)
            ins = self.cmd("line_set", *(["line_val"] if v else []),
                           note=f"{self.codec_pin} = {v} (idle) before the frame"
                           + (f", side {self.levels()}" if self.side else ""))
            ins.drives = dict({s.wire.text: int(self.sym.eval(s.value).v) for s in group},
                              **{self.codec_pin: v})
            return None
        if codec:
            v = int(self.sym.eval(codec[0].value).v)
            ins = self.cmd("line_set", *(["line_val"] if v else []),
                           note=f"{self.codec_pin} = {v}" + (f", side {self.levels()}" if self.side else ""))
        else:
            ins = self.emit("DLY", d=0, note=f"side {self.levels()}")
        ins.drives = {s.wire.text: int(self.sym.eval(s.value).v) for s in group}
        mark = self.label("t")
        ins.mark = mark
        if prev is not None and prev[2] is not None:
            self.placed(ins, prev, group[0].span)
        tol = self.tol_of(group, prev[3] if prev else None, gap) if gap is not None else None
        return mark, 0, gap, tol, last.span

    def levels(self):
        return ", ".join(f"{w}={v}" for w, v in self.side_level.items())

    def placed(self, ins, prev, span):
        """ins is the instant `for gap ~ tol` after prev: a tunable DLY in front of it."""
        pmark, poff, gap, tol, pspan = prev
        if tol is None:
            raise self.err("a placement without a tolerance (`~`)", pspan)
        k = self.prog.index(ins)
        d = Ins("DLY", {"d": 0, **({"ss": self.prog[k - 1].a.get("ss", 0)} if self.side else {})},
                tune=True, note="placement")
        self.prog.insert(k, d)
        ob = Ob("place", "R310", f"drive at line {span.line}, {float(gap):.2f} ticks after line {pspan.line}",
                span, pmark, offset=poff, ideal=gap, why=f"at {float(gap):.2f} ticks ~ {float(tol):.2f}")
        ob.tol = tol
        ins.ob = ob
        d.serves = ob

    def hold(self, prev):
        pmark, poff, gap, tol, span = prev
        long = int(gap) // 512
        for _ in range(long):
            self.emit("DLY", d=511, note="hold")
        d = self.emit("DLY", d=0, note="hold")
        d.tune = True
        ob = Ob("hold", "R313", "hold", span, pmark, lo=gap, offset=poff,
                why=f"at least {float(gap):.2f} ticks before the next state")
        d.serves = ob
        self.pending_hold = ob

    pending_hold = None

    def jump(self, target, cond="ALWAYS"):
        """A pending hold binds to the jump that leaves the state."""
        ins = self.emit("JMP", cond=cond, target=target)
        if cond == "ALWAYS" and self.pending_hold is not None:
            ins.ob, self.pending_hold = self.pending_hold, None
        return ins

    def repeat_await(self, s, abort):
        n = int(self.sym.eval(s.count).v)
        if len(s.body) != 1 or not isinstance(s.body[0], A.Await) or s.body[0].guard is not None:
            raise self.err("a `repeat` other than `repeat N { await E; }` in a frame role", s.span)
        ev = s.body[0].event
        a, sig = AE.lookup(self.sym, ev) if AE.named(ev) else (None, None)
        if a is None:
            raise self.err(f"await {printer.event(ev)} in a frame role", ev.span)
        if not 1 <= n <= 256:
            raise self.err("a repeat count outside 1..256", s.span)
        self.timer_event0(ev)
        self.emit("SET", dst="Y", imm=n - 1)
        top = self.label("rep")
        self.place(top)
        self.await_event(ev, a, sig, abort=abort)
        self.jump(top, "YDEC")

    def push(self, s):
        if s.pull:
            raise self.err("`pull host` inside a frame seq", s.span)
        st = self.flush_for.get(s.var.text) if hasattr(self, "flush_for") else None
        if st is None:
            raise self.err(f"push host {s.var.text}: only a received frame is pushed by a frame role",
                           s.span)
        st()

    # ------------------------------------------------------------ send
    def send(self, s):
        lay = frames.layout(self.sym, s.name.text, s.span)
        if self.codec_pin is None or self.codec_pin != self.tx_pin or self.period is None:
            raise self.err("`send` needs a line codec on an output wire and a bit period", s.span)
        bits, nfix = lay.prefix()
        k = lay.crc_field()
        var = s.args[0].name.text if s.args and isinstance(s.args[0], A.Ref) else None
        buf = self.buffer_of(var)
        if buf is None and (var is None or var != self.pulled):
            raise self.err("`send F(v)` needs v pulled from the host just before", s.span)
        if buf is not None and self.pulled is None:
            raise self.err(f"`send F({var})` from a data window array needs the header word pulled from "
                           "the host just before", s.span)
        self.pulled_used = self.pulled          # every way into this state must pull it (lower())
        if len(bits) % 32 or k != len(lay.fields) - 1 or lay.crc_field() is None \
                or lay.fields[k].crc[1] != lay.fields[nfix].name \
                or lay.fields[k].crc[2] != lay.fields[k - 1].name \
                or any(f.count for f in lay.fields):
            raise self.err(f"the layout of frame '{lay.frame.name.text}' for `send` (a constant start "
                           "of whole words, then host words, then the CRC over them)", s.span)
        if not lay.lsb_first:
            raise self.err("`send` of a most significant bit first frame", s.span)
        self.cfg["tx_crc"] = 1
        self.crc_prefix = bits
        if buf is not None:
            return self.send_buffered(s, lay, bits, nfix, k, var, buf)
        self.notes[f"pull host {var}"] = (
            "first word, the header: bits 15..0 = number of 32-bit words before the last one, minus "
            "1 (at least 1 word); bits 21..16 = 0x20 + bits in the last word - 1 (TXCTL). Then the "
            f"words of '{lay.fields[nfix].name}' to '{lay.fields[k - 1].name}', bit 0 first on the "
            "line, first octet in bits 7..0")
        P, need = self.tx_prefix(s, bits, "words before the last, minus 1")
        top, more, last = self.label("tx"), self.label("more"), self.label("last")
        self.place(top)
        p = self.emit("PULL", blk=1, note="next word")
        p.fw = FWait("HOST")
        self.emit("OUT", dst="X", n=16)
        self.emit("OUT", dst="Y", n=16)
        self.feed_wait("TXNEED", ("txw", 2 + 31 * P))
        ins = self.csrw("TXDATA")
        ins.mark = "txw"
        self.feed_ob(ins, "txneed", need, s.span, "TXDATA write after TXNEED")
        self.emit("MOV", dst="Y", src="ISR")
        self.jump(more, "YDEC")
        self.jump(last)
        self.place(more)
        self.emit("MOV", dst="ISR", src="Y")
        self.jump(top)
        self.place(last)
        p = self.emit("PULL", blk=1, note="last word")
        p.fw = FWait("HOST")
        self.tx_last(s, P, need)

    def tx_prefix(self, s, bits, count):
        """The header word in OSR (count, TXCTL of the last word), then the constant prefix words."""
        P = self.p_min
        need = AE.deadline("txneed", P)
        timer = self.assists.get("timer")
        if timer is not None:
            self.load_yx(self.timer_ctl(timer, 0))
            self.csrw("TIMER_CTL", "event 0 is TXNEED")
        self.emit("OUT", dst="X", n=16, note=f"header: {count}")
        self.emit("MOV", dst="ISR", src="X")
        self.emit("OUT", dst="T", n=16, note="header: TXCTL of the last word")
        self.cmd("crc_clear", note="the CRC starts with the constant prefix (CRC_INIT accounts for it)")
        first = True
        for w in range(0, len(bits), 32):
            word = sum(b << i for i, b in enumerate(bits[w:w + 32]))
            self.load_yx(word)
            if not first:
                self.feed_wait("TXNEED", ("txw", 2 + 31 * P))
            else:
                self.half_wait()
            ins = self.csrw("TXDATA", f"prefix word 0x{word:08X}")
            ins.mark = "txw"
            if not first:
                self.feed_ob(ins, "txneed", need, s.span, "TXDATA write after TXNEED")
            else:
                ins.ob = Ob("feed", "R316", "first TXDATA write after a half-bit strobe", s.span, "ev_HALF",
                            lo=0, hi=P - 2 - -(-(self.q16 // 2) // 16),
                            why="the first word must be in before the next bit start, or that bit's half "
                            "strobe drives the previous frame's last bit onto the line first")
            first = False
        return P, need

    def half_wait(self):
        """The bit timer runs freely, and the codec takes a half-bit strobe once TX is active: a first
        word written after a bit start but before its half strobe would put the last frame's final bit
        on the line for the rest of that bit. So the first word is written just after a half strobe:
        the strobe comes ceil(period / 2) cycles after the bit-start cycle's phase wraps, the next bit
        start P - 1 cycles after that wrap."""
        w = self.wait("FLAG", "HALF", note="a half-bit strobe: the first word goes in before the bit start")
        w.fw = FWait("HALF", mark="ev_HALF", pulse=True)
        w.react = True

    def tx_last(self, s, P, need):
        """The last word in OSR: its TXCTL, the write, then back to whole words and wait for idle."""
        self.emit("MOV", dst="X", src="T")
        self.csrw("TXCTL", "last word: its bits and the CRC append")
        self.emit("OUT", dst="X", n=16)
        self.emit("OUT", dst="Y", n=16)
        self.feed_wait("TXNEED", ("txw", 2 + 31 * P))
        ins = self.csrw("TXDATA")
        ins.mark = "txw"
        self.feed_ob(ins, "txneed", need, s.span, "TXDATA write after TXNEED")
        self.emit("SET", dst="X", imm=31)
        self.csrw("TXCTL", "back to 32 bits per word")
        w = self.wait("EV", "TXIDLE", note="the frame and its CRC are out: the line is idle")
        w.fw = FWait("TXIDLE", mark="txend", earliest=("txw", 2 + 32 * P))
        self.pulled = None

    def buffer_of(self, var):
        """(base, words) of a data window array named var, None if var is not one."""
        v = self.vars.get(var)
        if v is None or v.type.name.text != "words":
            return None
        return DATA_BASE * self.lane, int(self.sym.eval(v.type.width).v)

    def send_buffered(self, s, lay, bits, nfix, k, var, buf):
        """The host words come from a data window array the host wrote beforehand, the last word at
        the array's first word and the first word highest, so one register walks down to the base.
        The host's mailbox is then needed only for the header, not at the line's pace."""
        base, n = buf
        most = -(-(lay.offset(k)[1] - len(bits)) // 32)
        if n > DATA_BASE or most > n:
            raise TseError("R308", f"`send {lay.frame.name.text}({var})`: frame '{lay.frame.name.text}' "
                           f"needs up to {most} words after its constant start, the array has {n} and a "
                           f"frame role has {DATA_BASE} data window words per lane", s.span)
        self.windows = [(base, n)]
        self.notes[f"pull host {self.pulled}"] = (
            f"the header, after the frame is in '{var}' (data window words {base} to {base + n - 1}): "
            f"bits 15..0 = {base} + the number of 32-bit words before the last one (at least 1); bits "
            "21..16 = 0x20 + bits in the last word - 1 (TXCTL)")
        self.notes[f"data window {var}"] = (
            f"the words of '{lay.fields[nfix].name}' to '{lay.fields[k - 1].name}' in reverse: the last "
            f"word in word {base}, the one before it in word {base + 1}, and so on; bit 0 first on the "
            "line, first octet in bits 7..0")
        P, need = self.tx_prefix(s, bits, "data window word of the first frame word")
        top, dec, last = self.label("tx"), self.label("dec"), self.label("last")
        self.place(top)
        self.emit("MOV", dst="X", src="ISR")
        self.emit("LDW", ind=1, addr=0, note="next word from the data window")
        self.emit("OUT", dst="X", n=16)
        self.emit("OUT", dst="Y", n=16)
        self.feed_wait("TXNEED", ("txw", 2 + 31 * P))
        ins = self.csrw("TXDATA")
        ins.mark = "txw"
        self.feed_ob(ins, "txneed", need, s.span, "TXDATA write after TXNEED")
        self.emit("MOV", dst="X", src="ISR")
        self.emit("SET", dst="Y", imm=base)
        self.jump(dec, "XDEC")
        self.place(dec)
        self.emit("MOV", dst="ISR", src="X")
        self.jump(top, "XNEY")
        self.place(last)
        self.emit("LDW", addr=base, note="last word")
        self.tx_last(s, P, need)

    def feed_wait(self, key, earliest, alt=None):
        w = self.wait("EV", key, alt=alt is not None)
        w.fw = FWait(key, mark="ev_" + key, earliest=earliest, alt=alt)
        w.react = True
        return w

    def feed_ob(self, ins, fact, hi, span, name, mark=None, lo=0):
        ins.ob = Ob("feed", "R316", name, span, mark or "ev_" + fact.upper().replace("EOP", "ERR"),
                    lo=lo, hi=hi, why=AE.FACTS[fact][1])

    # ------------------------------------------------------------ recv
    def recv(self, s, target):
        lay = frames.layout(self.sym, s.name.text, s.span)
        if self.period is None or self.rx_pin is None:
            raise self.err("`recv` needs a DPLL and an input wire", s.span)
        if lay.lsb_first is None:
            raise self.err(f"frame '{lay.frame.name.text}' has no CRC, so its bit order is not stated",
                           s.span)
        self.cfg["rx_crc"] = 1
        self.cfg["rx_msb"] = 1          # bits enter RXDATA at bit 0: an 8-bit word is X[7:0]
        tgt = self.states[target]
        var = s.into.text if s.into is not None else None
        if any(f.count for f in lay.fields):
            st = self.recv_counted(lay, s, var)
        else:
            st = self.recv_open(lay, s, var)
        st.frame = lay
        self.status_for[target] = st
        self.done.add(target)
        self.entry_level[target] = dict(self.side_level)
        self.queue.insert(0, target)
        del tgt

    def counted_shape(self, lay, s):
        v = lay.variable()
        k = lay.crc_field()
        mine = lay.first_sent_by(self.role.name.text)
        if len(v) != 1 or lay.fields[v[0]].count is None or k is None or mine is None \
                or mine <= k or k != v[0] + 1 or lay.index(lay.fields[v[0]].count) != v[0] - 1:
            raise self.err(f"the layout of frame '{lay.frame.name.text}' for `recv` (a header ending in "
                           "the byte count, the bytes, the CRC, then our own field)", s.span)
        head = lay.offset(v[0])[0]
        tail = sum(f.lo for f in lay.fields[k:mine])
        if head > 32 or tail > 32 or lay.fields[mine].lo > 16:
            raise self.err("a header, CRC tail or answer field longer than one RX word", s.span)
        return v[0], k, mine, head, tail

    def recv_counted(self, lay, s, var):
        """Header word (up to the byte count), the bytes, the CRC tail word; the response rule's
        position follows the byte count. The frame is kept in the data window until its CRC is
        known, so only a good frame reaches the host."""
        iv, k, mine, head, tail = self.counted_shape(lay, s)
        nmax = lay.fields[iv].hi // 8
        P, base = self.p_min, DATA_BASE * self.lane
        self.cfg["rx_len_m1"] = head - 1
        if not lay.fields[k].nostuff and any(f.nostuff for f in lay.fields[:k]):
            raise self.err("a nostuff field inside the stuffed part of the frame", s.span)
        pos0 = head + lay.fields[k].lo + (tail - lay.fields[k].lo) - 1     # the bit before our field
        ack = self.find_ack(lay, mine, s)
        self.rules.append(Rule(match=0, resp=ack[0], pos=254, rlen=lay.fields[mine].lo, offset=1,
                               cond="CRC_GOOD"))
        w = self.wait("EV", "RXFULL", note=f"header: {head} bits")
        w.fw = FWait("RXFULL", mark="rx", earliest=("arm", head * P))
        w.react = True
        r = self.emit("CSRR", addr=REG["RXDATA"], note="header")
        self.feed_ob(r, "rxfull", AE.deadline("rxfull", P), s.span, "RXDATA read after RXFULL", "rx")
        self.emit("IN", src="Y", n=16)
        self.emit("IN", src="X", n=16)
        self.emit("STW", addr=base, note="header word")
        self.emit("MOV", dst="OSR", src="X")
        cnt = lay.fields[iv].count
        cw = lay.get(cnt).lo
        self.emit("OUT", dst="X", n=cw, note=f"X = {cnt}")
        common, zero = self.label("count"), self.label("none")
        pos_hi = (pos0 - (head - 1)) * P - 2 - AE.CFG_VISIBLE
        for n in range(nmax):
            nxt = self.label("n")
            self.emit("SET", dst="Y", imm=n)
            self.jump(nxt, "XNEY")
            self.emit("SET", dst="Y", imm=pos0 + 8 * n, note=f"{cnt} = {n}: answer after bit {pos0 + 8 * n}")
            self.jump(zero if n == 0 else common)
            self.place(nxt)
        self.emit("SET", dst="X", imm=nmax, note=f"{cnt} >= {nmax}: {nmax} bytes")
        self.emit("SET", dst="Y", imm=pos0 + 8 * nmax)
        self.place(common)
        self.emit("MOV", dst="OSR", src="X", note="bytes to come")
        self.rule_pos(ack, pos_hi, s.span)
        self.rx_len(7, 8 * P - 2 - AE.CFG_VISIBLE, s.span)
        loop, t1, t2, done = self.label("byte"), self.label("t"), self.label("t"), self.label("done")
        self.place(loop)
        w = self.wait("EV", "RXFULL", note="a byte")
        w.fw = FWait("RXFULL", mark="rx", earliest=("rx", 8 * P))
        w.react = True
        r = self.emit("CSRR", addr=REG["RXDATA"])
        self.feed_ob(r, "rxfull", AE.deadline("rxfull", P), s.span, "RXDATA read after RXFULL", "rx")
        self.emit("IN", src="X", n=8)
        self.emit("MOV", dst="X", src="OSR")
        self.jump(t1, "XDEC")
        self.place(t1)
        self.emit("MOV", dst="OSR", src="X")
        self.emit("SET", dst="Y", imm=4)
        self.jump(t2, "XNEY")
        self.emit("STW", addr=base + 1, note="bytes 1..4 of 8")
        self.place(t2)
        self.jump(done, "XZ")
        self.jump(loop)
        self.place(done)
        self.emit("STW", addr=base + 2, note="the last bytes")
        tl = self.label("tail")
        self.rx_len(tail - 1, 8 * P - 2 - AE.CFG_VISIBLE, s.span)
        self.header_bits = head
        self.jump(tl)
        self.place(zero)
        self.emit("MOV", dst="OSR", src="X")
        self.rule_pos(ack, pos_hi, s.span)
        self.rx_len(tail - 1, tail * P - 2 - AE.CFG_VISIBLE, s.span)
        self.place(tl)
        w = self.wait("EV", "RXFULL", note=f"CRC and the bits up to our field: {tail} bits")
        w.fw = FWait("RXFULL", mark="rx", earliest=("rx", tail * P))
        r = self.emit("CSRR", addr=REG["RXDATA"], note="drop the tail: only the read clears RXFULL")
        self.feed_ob(r, "rxfull", AE.deadline("rxfull", P), s.span, "RXDATA read after RXFULL", "rx")
        good, bad = self.label("good"), self.label("bad")
        t = self.wait("EV", "RULEHIT", to=True, note="the rule matched: CRC good")
        t.fw = FWait("RULEHIT", test=True)
        self.jump(bad, "TOUT")
        self.jump(good)
        self.notes[f"push host {var}"] = (
            "three blocking pushes per good frame: the header (bits "
            f"{head - 1}..0, first bit highest), then the data bytes right-aligned in two words "
            f"(the last byte in bits 7..0 of the second word); {cnt} says how many are valid")

        def flush():
            for i in range(3):
                self.emit("LDW", addr=base + i)
                self.emit("IN", src="OSR", n=32)
                p = self.emit("PUSH", blk=1, note=f"frame word {i}")
                p.fw = FWait("HOST")
        self.flush_for = {var: flush}
        self.windows = [(base, 3)]
        return Status(good, bad, ack=ack)

    def find_ack(self, lay, mine, s):
        f = lay.fields[mine]
        self.ack_field = f
        return (0, f)

    def rule_pos(self, ack, hi, span):
        # X is the answer (RESP, bits 15..0): the respond is lowered later, so lower() fills it in
        self.resp_sets.append((self.emit("SET", dst="X", imm=0), span))
        ins = self.csrw("RULE0_RESP_POS", "answer position")
        self.feed_ob(ins, "rxfull", hi, span, "rule position written before the earliest answer "
                     "position", "rx")
        ins.ob.why = "the response rule must hold the new position before the bit counter reaches it"

    def rx_len(self, n, hi, span):
        self.load_yx(self.lane_cfg_with(rx_len_m1=n))
        ins = self.csrw("LANE_CFG", f"RX words of {n + 1} bits")
        self.feed_ob(ins, "rxfull", hi, span, f"RX word length {n + 1} set in time", "rx")
        ins.ob.why = "the new RX word length must be in use before the old one would end a word"

    def lane_cfg_with(self, **kv):
        f = dict(self.cfg, **kv)
        return regs.pack("LANE_CFG", **f)

    def recv_open(self, lay, s, var):
        """Hunt for the constant prefix with response rule 0, then 8-bit RX words until the codec
        reports the end of the frame; the words stream to the host as they arrive."""
        bits, nfix = lay.prefix()
        k = lay.crc_field()
        codec = self.assists.get("manchester")
        if not bits or len(bits) > 16 or k != len(lay.fields) - 1 or codec is None \
                or lay.fields[k].crc[1] != lay.fields[nfix].name \
                or lay.fields[k].crc[2] != lay.fields[k - 1].name \
                or any(f.lo % 8 or f.hi % 8 for f in lay.fields[nfix:]):
            raise self.err(f"the layout of frame '{lay.frame.name.text}' for `recv` without a byte count "
                           "(a constant start of up to 16 bits, octets, the CRC over them last, and a "
                           "Manchester codec to end it)", s.span)
        P = self.p_min
        pat = 0
        for b in bits:
            pat = (pat << 1) | b
        self.rules.append(Rule(match=pat | ((1 << len(bits)) - 1) << 16, any=1, drive=0))
        self.cfg["rx_len_m1"] = 7
        self.crc_extra = [1]            # the first idle cell decodes as 1 and enters the CRC
        self.set_alt((1, "EV", WIDX["EV"]["ERR"]), s.span)
        w = self.wait("EV", "RULEHIT", note="the frame's constant start")
        w.fw = FWait("RULEHIT", mark="hit", earliest=("arm", len(bits) * P))
        w.react = True
        r = self.cmd("frame_rst", "crc_clear", note="frame starts: RX words and the CRC from here")
        # One mark for the reset and every RXFULL: the octet waits measure from the latest of them
        # (a mark of the reset alone would be stale on every pass after the first).
        r.mark = "rx"
        r.ob = Ob("feed", "R316", "frame reset after the start matched", s.span, "hit", lo=0,
                  hi=AE.deadline("rulehit", P), why=AE.FACTS["rulehit"][1])
        top, rx1 = self.label("rx"), self.label("rx")
        good, bad = self.label("good"), self.label("bad")
        ends = [self.label(f"end{j}_") for j in range(4)]

        def octet(j, earliest, err):
            w = self.wait("EV", "RXFULL", alt=True, note=f"octet {j}, or the end of the frame")
            w.fw = FWait("RXFULL", mark="rx", earliest=("rx", earliest), alt="ERR", alt_mark="err",
                         alt_earliest=("rx", err))
            w.react = True
            w.odd = True
            self.jump(ends[j], "TOUT")
            rd = self.emit("CSRR", addr=REG["RXDATA"])
            self.feed_ob(rd, "rxfull", AE.deadline("rxfull", P), s.span, "RXDATA read after RXFULL", "rx")
            self.emit("IN", src="X", n=8)
        # The first octet after the reset: its first bit may enter 1 cycle on (7 periods and 1
        # cycle), and ERR (cleared by the reset) needs a cell decoded after it. Every later octet:
        # 8 periods after the last RXFULL, and ERR, from a cell sampled at least a period after
        # that octet's last bit, is seen at least P - 1 cycles after RXFULL was.
        octet(0, 7 * P + 1, 1)
        self.jump(rx1)
        self.place(top, align=True)
        for j in range(4):
            if j == 1:
                self.place(rx1)
            octet(j, 8 * P, P - 1)
        self.emit("PUSH", note="four octets")
        self.jump(top)
        for j, end in enumerate(ends):
            self.place(end)
            t = self.wait("EV", "CRCOK", to=True, note="CRC good, frame plus one idle bit")
            t.fw = FWait("CRCOK", test=True)
            t.ob = Ob("feed", "R316", "CRC test after the end of the frame", s.span, "err", lo=1,
                      hi=AE.deadline("eop", P), why=AE.FACTS["eop"][1])
            nb = self.label("b")
            self.jump(nb, "TOUT")
            self.emit("SET", dst="X", imm=2 * j + 1, note=f"status: good, {j} octets in the last word")
            self.jump(good)
            self.place(nb)
            self.emit("SET", dst="X", imm=2 * j, note=f"status: bad, {j} octets in the last word")
            self.jump(bad)
        self.notes[f"push host {var}"] = (
            "streamed: a word per 4 octets as they arrive (the push does not block: read each within 4 "
            "octet times or lose it), reverse its 32 bits to get octet i in bits 8i+7..8i; at the end "
            "a word with the last octets in its low bits, then a status word: bit 0 = CRC good, bits "
            "2..1 = octets in that last word")
        self.warnings.append(TseError(
            "W271", f"frame '{lay.frame.name.text}' does not fit the data window, so '{var}' goes to the "
            "host while it arrives; a frame with a bad CRC still ends with a status word (good = 0) so "
            "the host can drop it", s.span))

        def flush():
            for what in ("last octets", "status"):
                p = self.emit("PUSH", blk=1, note=what)
                p.fw = FWait("HOST")
                if what == "last octets":
                    self.emit("IN", src="X", n=16)
        self.flush_for = {var: flush}
        return Status(good, bad, stream=True)

    # ------------------------------------------------------------ status states
    def status_state(self, st, stat):
        """The edges after a `recv`: the frame's CRC result and end of frame, and a respond that
        answers in the frame's own field through response rule 0."""
        crc = stat.frame.crc.name.text
        good, bad = None, None
        for e in st.edges:
            ev = e.event if isinstance(e, (A.OnEdge, A.RespondEdge)) else None
            if ev is None or not AE.named(ev) or getattr(e, "guard", None) is not None:
                raise self.err(f"edges of state '{st.name.text}' after a `recv` other than the CRC "
                               "result and the end of frame", st.name.span)
            a, sig = AE.lookup(self.sym, ev)
            ok = a.name.text == crc and ev.attr.text == "ok"
            if (good if ok else bad) is not None:
                raise self.err(f"two edges on the same frame result in state '{st.name.text}'", e.span)
            if ok:
                good = e
            else:
                bad = e
        if good is None or bad is None:
            raise self.err(f"state '{st.name.text}' after a `recv` must handle both a good and a bad "
                           "CRC", st.name.span)
        self.place(stat.bad)
        if stat.stream:
            self.flush_for[next(iter(self.flush_for))]()
        elif stat.ack is not None:
            self.field_end(stat)
        self.mark_frame_end(stat, bad=True)
        self.goto(bad.target.text)
        self.place(stat.good)
        if isinstance(good, A.RespondEdge):
            self.respond(good, stat)
        elif stat.ack is not None:
            self.field_end(stat)
        else:
            self.mark_frame_end(stat, bad=False)
        self.goto(good.target.text)

    def mark_frame_end(self, stat, bad):
        if stat.stream:
            self.set_rearm(stat, "err")

    def respond(self, e, stat):
        if stat.ack is None:
            raise self.err(f"respond {e.name.text}: only an answer in the received frame's own "
                           "field is lowered", e.span)
        f = stat.ack[1]
        body = e.body
        if len(body) != 1 or not isinstance(body[0], A.Drive) or body[0].wire.text != self.rule_pin:
            raise self.err(f"respond {e.name.text}: the answer must be one drive of the rule's pin",
                           e.span)
        d = body[0]
        v = int(self.sym.eval(d.value).v)
        n = f.lo
        if d.timing.for_ is not None:
            length = self.sym.eval(d.timing.for_).v * self.f
            if abs(length - n * self.period) > Fr(1, 2):
                raise self.err(f"respond {e.name.text}: drives for {float(length):.1f} ticks, the field "
                               f"'{f.name}' is {n} bit(s)", d.span)
        self.rules[0].resp = v * ((1 << n) - 1)
        lo = self.sym.eval(e.lo).v * self.f
        hi = self.sym.eval(e.hi).v * self.f
        self.ack_window = (e, lo, hi, f)
        self.warnings.append(TseError(
            "W270", f"respond {e.name.text} on {printer.event(e.event)}: the answer goes into field "
            f"'{f.name}' (from {self.role.name.text}) of the frame just received, so its window is "
            f"measured from that field's start as the bit timer sees it, not from the CRC result",
            e.span))
        self.field_end(stat)

    def field_end(self, stat):
        """Wait until our own field is over, answered or not: another node answering in it
        (a dominant CAN ACK) must not look like the next start of frame."""
        n = stat.ack[1].lo
        for i in range(n + 1):
            w = self.wait("EV", "BITSTART", note="answer field starts" if i == 0 else "answer done")
            w.fw = FWait("BITSTART", pulse=True, mark="fend" if i == n else None)
        # back to header words for the next frame; no deadline: the arm comes later in program
        # order and the header's first word ends many bits after it
        self.load_yx(self.lane_cfg_with(rx_len_m1=self.header_bits - 1))
        self.csrw("LANE_CFG", f"RX words of {self.header_bits} bits again")
        self.set_rearm(stat, "fend")

    def set_rearm(self, stat, mark):
        """The next frame's start may come as soon as the datasheet allows after this one's end."""
        start = next((e.event for s in self.role.states for e in s.edges
                      if isinstance(e, A.OnEdge) and e.event.kind in ("rise", "fall", "edge")
                      and e.event.name.text == self.rx_pin), None)
        if start is None:
            return
        w = A.Ident(self.rx_pin)
        if stat.stream:
            end = A.Event("edge", w)
            delay = self.period / 2 + self.period * self.sample + 3     # last edge to ERR seen
        else:
            end = A.Event("rise", w)
            delay = Fr(0)
        sep, rules = min_separation(self.sym, self.role, end, start, self.f)
        codec = self.assists.get("manchester")
        if stat.stream and codec is not None:
            # A gap between frames is a rule from the codec's end of frame (the frame's last
            # edge, as the line shows it) to the next start: edge to edge rules hold inside a frame
            eop = A.Event("named", A.Ident(codec.name.text), A.Ident("eop"))
            t, r = min_separation(self.sym, self.role, eop, start, self.f)
            if r and t > sep:
                sep, rules, end = t, r, eop
        quiet, tail = self.quiet_tail(stat, start)
        sep += quiet
        hi = int(sep - delay) - 1
        names = ", ".join(r.name.text for r in rules) if rules else "no datasheet rule"
        if tail:
            names += f"; the frame's own fields {tail} come first"
        self.rearm = Ob("rearm", "R314", "start-of-frame detection armed again", start.span, mark,
                        lo=None, hi=hi,
                        why=f"the next {printer.event(start)} may come {float(sep):.2f} ticks after "
                        f"{printer.event(end)} ({names})")
        self.rearm.sep, self.rearm.delay, self.rearm.end = sep, delay, end
        for ins in self.prog:
            if getattr(ins, "mark", None) == "arm":
                ins.ob = self.rearm

    def quiet_tail(self, stat, start):
        """(ticks, names) the frame still lasts after our answer field when its last fields are
        constants that cannot hold the start edge (a CAN ACK delimiter and end of frame), so the
        next start cannot come before they are over. One bit is given up for the bit timer's
        phase at the field end; every other bit counts at the shortest resynchronised period."""
        if stat.ack is None or start.kind not in ("rise", "fall"):
            return Fr(0), ""
        lay = stat.frame
        i = lay.fields.index(stat.ack[1])
        tail = lay.fields[i + 1:]
        if not tail or any(f.const is None or not f.fixed for f in tail):
            return Fr(0), ""
        bits = [b for f in tail for b in lay.bits_of(f)]
        if any(b != (1 if start.kind == "fall" else 0) for b in bits):
            return Fr(0), ""
        return (len(bits) - 1) * self.p_min, f"{tail[0].name}..{tail[-1].name} ({len(bits)} bits)"


def is_host_pull(ev):
    return ev is not None and ev.kind == "named" and ev.name.text == "host" and ev.attr is not None \
        and ev.attr.text == "pull"

