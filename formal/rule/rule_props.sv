// Formal harness for tse_resp_rule (two rules, 16-bit patterns), immediate
// assertions only. Configuration is free and constant: both modes, both
// edges, any tick delay, pattern, mask, position, offset, length, condition,
// abort, drive and chain bits. Inputs are free in every cycle.
//  REF:    a reference written from the rule's definition steps beside the
//          unit; drive enable, drive value, hit flags, hit and abort pulses
//          must equal it in every cycle. The reference paces a response by
//          counting cycles since the last counted edge (pin-edge mode with a
//          tick delay) and counting pacing events down from the offset, where
//          the unit loads a tick counter per edge.
//  TIMING: when a response starts, the pacing events (bit starts in bit
//          mode, counted edges in pin-edge mode) seen since the match that
//          scheduled it number exactly the offset, and in pin-edge mode the
//          last counted edge was exactly tick delay + 1 cycles earlier; every
//          later change of the drive is tick delay + 1 cycles after a counted
//          edge too.
//  ONCE:   a fixed-position rule (pos + offset <= 254) matches at most once
//          per frame (stated on the reference's matches, which REF ties to
//          the unit's hit flags and pulses).
//  CHAIN:  a chained rule 1 matches only while rule 0's hit flag is already
//          set; rule 1 without chain does not look at it.
// Environment, as the compiler guarantees it: in pin-edge mode with a tick
// delay no two counted edges are closer than tick delay + 3 cycles.
//  HOLD:   with RULE_CLK.hold the drive keeps its last bit after the
//          response and ends exactly at the clock edge after the sequencer
//          writes the pin (seq_wr), unless a new match takes over then.
module rule_props (
  input logic clk,
  input logic frame_rst_in,
  input logic bit_valid,
  input logic bit_in,
  input logic bit_start,
  input logic pin_clk,
  input logic pin_dat,
  input logic crc_ok,
  input logic ext_flag,
  input logic seq_wr
);
  localparam int K = 2, PW = 16, CW = 14;
  (* anyconst *) logic [K*PW-1:0] pat, msk;
  (* anyconst *) logic [K*9-1:0]  pos;
  (* anyconst *) logic [K*16-1:0] resp;
  (* anyconst *) logic [K*CW-1:0] ctl;
  (* anyconst *) logic            src, edge_sel, hold;
  (* anyconst *) logic [7:0]      tdly;

  logic init = 1'b1;
  always_ff @(posedge clk) init <= 1'b0;
  wire frame_rst = !init && frame_rst_in;

  logic         drv_en, drv_val, hit_pulse, abort_pulse;
  logic [K-1:0] hit_flags;
  tse_resp_rule dut (
    .clk, .rst_n(!init), .cfg_pat(pat), .cfg_msk(msk), .cfg_pos(pos), .cfg_resp(resp),
    .cfg_ctl(ctl), .cfg_src(src), .cfg_edge(edge_sel), .cfg_tdly(tdly), .cfg_hold(hold), .seq_wr, .frame_rst,
    .bit_valid, .bit_in, .bit_start, .pin_clk, .pin_dat, .crc_ok, .ext_flag,
    .flags_clr(frame_rst), .drv_en, .drv_val, .hit_flags, .hit_pulse, .abort_pulse
  );

  // control word fields of rule k
  function automatic logic [3:0] f_len(input int k);  f_len = ctl[CW*k +: 4];       endfunction
  function automatic logic [3:0] f_off(input int k);  f_off = ctl[CW*k + 4 +: 4];   endfunction
  function automatic logic [1:0] f_cond(input int k); f_cond = ctl[CW*k + 8 +: 2];  endfunction
  function automatic logic f_drive(input int k); f_drive = ctl[CW*k + 10]; endfunction
  function automatic logic f_abort(input int k); f_abort = ctl[CW*k + 11]; endfunction
  function automatic logic f_en(input int k);    f_en = ctl[CW*k + 12];    endfunction
  function automatic logic f_chain(input int k); f_chain = ctl[CW*k + 13]; endfunction

  // counted strobe and shifted bit
  logic clk_q, dat_q;
  wire  edge_now = edge_sel ? (clk_q && !pin_clk) : (!clk_q && pin_clk);
  wire  bv = src ? edge_now : bit_valid;
  wire  bi = src ? dat_q : bit_in;

  // cycles since the last counted edge, saturating
  logic [8:0] since;
  wire  delayed = src && tdly != 8'd0;
  wire  pace = !src ? bit_start : delayed ? (since == {1'b0, tdly}) : edge_now;
  always_comb if (!init && delayed && edge_now) assume (since >= {1'b0, tdly} + 9'd3);

  // history and frame bit counter
  logic [PW-1:0] hist;
  logic [7:0]    cnt;
  wire  [PW-1:0] win = {hist[PW-2:0], bi};

  logic [K-1:0] flags;
  logic [K-1:0] m;
  always_comb
    for (int k = 0; k < K; k++) begin
      logic c_ok;
      case (f_cond(k))
        2'd1: c_ok = crc_ok;
        2'd2: c_ok = !crc_ok;
        2'd3: c_ok = ext_flag;
        default: c_ok = 1'b1;
      endcase
      m[k] = f_en(k) && bv && c_ok && !(k == 1 && f_chain(1) && !flags[0])
             && ((win ^ pat[PW*k +: PW]) & msk[PW*k +: PW]) == '0
             && (pos[9*k + 8] || pos[9*k +: 8] == cnt);
    end
  wire w1 = !m[0] && m[1];                    // rule 0 wins a tie
  wire any_m = |m;
  wire drive_m = m[0] ? f_drive(0) : (m[1] && f_drive(1));
  wire abort_m = m[0] ? f_abort(0) : (m[1] && f_abort(1));
  wire [3:0]  w_off = w1 ? f_off(1) : f_off(0);
  wire [3:0]  w_len = w1 ? f_len(1) : f_len(0);
  wire [15:0] w_resp = w1 ? resp[31:16] : resp[15:0];

  // response: wait for `todo` pacing events, then drive one bit per event
  logic        r_en, r_wait, r_hit, r_abort;
  logic [4:0]  todo;
  logic [3:0]  left;
  logic [15:0] bits;
  always_ff @(posedge clk)
    if (init) begin
      clk_q <= 1'b0; dat_q <= 1'b0; since <= 9'h1ff; hist <= '0; cnt <= '0;
      flags <= '0; r_en <= 1'b0; r_wait <= 1'b0; r_hit <= 1'b0; r_abort <= 1'b0;
      todo <= '0; left <= '0; bits <= '0;
    end else begin
      clk_q <= pin_clk;
      dat_q <= pin_dat;
      if (src && edge_now) since <= 9'd1;
      else if (since != 9'h1ff) since <= since + 9'd1;
      if (frame_rst) begin
        hist <= '0; cnt <= '0;
      end else if (bv) begin
        hist <= win;
        cnt  <= (cnt == 8'hff) ? cnt : cnt + 8'd1;
      end
      flags   <= frame_rst ? '0 : (flags | m);
      r_hit   <= any_m;
      r_abort <= abort_m;
      if (drive_m) begin
        bits <= w_resp; left <= w_len;
        if (w_off == 4'd0 && !delayed) begin
          r_en <= 1'b1; r_wait <= 1'b0;
        end else begin
          r_wait <= 1'b1; todo <= {1'b0, w_off} + (delayed ? 5'd1 : 5'd0);
        end
      end else if (pace) begin
        if (r_wait) begin
          todo <= todo - 5'd1;
          if (todo == 5'd1) begin r_wait <= 1'b0; r_en <= 1'b1; end
        end else if (r_en) begin
          if (left == 4'd0) r_en <= hold;
          else begin bits <= bits >> 1; left <= left - 4'd1; end
        end
      end
      if (hold && seq_wr && !drive_m) r_en <= 1'b0;
    end

  // timing monitors: pacing sources since the scheduling match, cycles since
  // the last counted edge, drive enable of the previous cycle, frame matches
  logic [4:0] seen;
  logic [3:0] off_q;
  logic       en_q, val_q, sched;
  logic [1:0] nmatch [K];
  wire  psrc = src ? edge_now : bit_start;
  always_ff @(posedge clk)
    if (init) begin
      seen <= '0; off_q <= '0; en_q <= 1'b0; val_q <= 1'b0; sched <= 1'b0;
      nmatch[0] <= '0; nmatch[1] <= '0;
    end else begin
      en_q  <= drv_en;
      val_q <= drv_val;
      if (drive_m) begin
        seen <= '0; off_q <= w_off; sched <= 1'b1;
      end else if (psrc && seen != 5'h1f) seen <= seen + 5'd1;
      if (drv_en && !en_q) sched <= 1'b0;
      for (int k = 0; k < K; k++)
        if (frame_rst) nmatch[k] <= 2'd0;
        else if (m[k] && nmatch[k] != 2'd3) nmatch[k] <= nmatch[k] + 2'd1;
    end

  logic flags0_q, flags1_q, wr_q;
  always_ff @(posedge clk) begin
    flags0_q <= init ? 1'b0 : hit_flags[0];
    flags1_q <= init ? 1'b0 : hit_flags[1];
    wr_q     <= init ? 1'b0 : seq_wr;
  end

  // The unit's registers, connected by the proof script (connect -set after
  // flatten), so the reference can be tied to them: a refinement map that
  // makes induction close in a few steps. Planted-bug runs (BUGRUN) leave
  // them out, so a bug must be caught by the named property itself.
`ifndef BUGRUN
  logic          p_clk_q, p_dat_q, p_pending;
  logic [7:0]    p_tk, p_cnt;
  logic [PW-1:0] p_hist;
  logic [4:0]    p_wait;
  logic [3:0]    p_left;
  logic [15:0]   p_rsr;
  always_comb if (!init) begin
    assert (p_clk_q == clk_q && p_dat_q == dat_q);
    assert (p_hist == hist && p_cnt == cnt);
    assert (p_pending == r_wait && drv_en == r_en);
    if (r_wait) assert (p_wait == todo && todo != 5'd0);
    if (drv_en || r_wait) assert (p_left == left && p_rsr == bits);
    assert (p_tk == ((delayed && since <= {1'b0, tdly}) ? 8'(9'(tdly) + 9'd1 - since) : 8'd0));
    assert (hit_flags == flags && hit_pulse == r_hit && abort_pulse == r_abort);
    if (sched && r_wait)
      assert ({1'b0, seen} + {1'b0, todo} == {2'b0, off_q} + ((delayed && since <= {1'b0, tdly}) ? 6'd1 : 6'd0));
    if (sched && r_wait) assert (seen <= {1'b0, off_q});
    for (int k = 0; k < K; k++)
      if (nmatch[k] != 2'd0 && !pos[9*k + 8]) assert (cnt > pos[9*k +: 8] || cnt == 8'hff);
  end
`endif

  always_comb if (!init) begin
`ifdef REF
    assert (drv_en == r_en);
    assert (drv_val == bits[0]);
    assert (hit_flags == flags);
    assert (hit_pulse == r_hit);
    assert (abort_pulse == r_abort);
`endif
`ifdef TIMING
    if (drv_en && !en_q && sched) begin
      assert (seen == {1'b0, off_q});
      if (src) assert (since == {1'b0, tdly} + 9'd1);
    end
    if (src && en_q && (drv_en != en_q || drv_val != val_q) && !(drv_en && !en_q))
      assert (since == {1'b0, tdly} + 9'd1 || hit_pulse || (hold && wr_q && !drv_en));
`endif
`ifdef ONCE
    for (int k = 0; k < K; k++) begin
      if (!pos[9*k + 8] && {1'b0, pos[9*k +: 8]} + {5'd0, f_off(k)} <= 9'd254)
        assert (nmatch[k] <= 2'd1);
    end
`endif
`ifdef HOLD
    if (en_q && !drv_en) assert (!hold || wr_q);
    if (hold && wr_q && en_q && !hit_pulse) assert (!drv_en);
`endif
`ifdef CHAIN
    if (hit_flags[1] && !flags1_q && f_chain(1)) assert (flags0_q);
`endif
  end

  always @(posedge clk) if (!init) begin
    cover (drv_en && src && tdly == 8'd3 && f_off(0) == 4'd2 && f_len(0) == 4'd1);
    cover (hit_flags == 2'b11 && f_chain(1));
    cover (hold && en_q && !drv_en && !src);
  end
endmodule
