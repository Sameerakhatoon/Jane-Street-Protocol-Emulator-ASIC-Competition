// Formal harness for tse_prog_mem (two lanes, flop build of the fallback
// store), immediate assertions only. The SRAM macro is a black box: its read
// data is free, which is all a timing proof needs.
//  NI:   two copies share lane K's fetch and data requests (address, window,
//        fallback select); the other lane, the host and the self-test port do
//        anything at all, independently in each copy. Lane K's fetch grants
//        and data grants are identical in both copies, for SRAM and fallback
//        fetches alike, so nobody else changes lane K's timing.
//  OWN:  in copy a, lane K is granted a fetch only when cnt mod 4 = K and a
//        data access only when cnt = 4K + 3, from the SRAM or the fallback
//        store; in the cycle after a fallback grant the fetch data is the
//        fallback word lane K asked for, whatever the host does; and with RUN
//        taking effect at a frame end (cnt = 15), a held request is granted at
//        a cycle of the lane's own count (cycles since RUN) that is K mod 4,
//        within 4 cycles, and data at 4K + 3 mod 16.
module pmem_props #(
  parameter int K = 1
) (
  input logic        clk,
  input logic        run_in,
  input logic        req_k, fb_k, dreq_k, dwe_k,
  input logic [6:0]  addr_k, daddr_k,
  input logic [1:0]  win_k,
  input logic [31:0] dwdata_k,
  input logic [1:0]  l_req_a, l_req_b, l_fb_a, l_fb_b, d_req_a, d_req_b, d_we_a, d_we_b,
  input logic [13:0] l_addr_a, l_addr_b, d_addr_a, d_addr_b,
  input logic [3:0]  l_win_a, l_win_b,
  input logic [63:0] d_wdata_a, d_wdata_b,
  input logic        h_req_a, h_req_b, h_we_a, h_we_b, h_dw_a, h_dw_b, h_fb_a, h_fb_b,
  input logic [8:0]  h_addr_a, h_addr_b,
  input logic [31:0] h_wdata_a, h_wdata_b,
  input logic        t_en_a, t_en_b, t_req_a, t_req_b, t_we_a, t_we_b,
  input logic [8:0]  t_addr_a, t_addr_b,
  input logic [31:0] t_wdata_a, t_wdata_b,
  input logic        clr_a, clr_b
);
  localparam int L = 2;
  logic init = 1'b1;
  always_ff @(posedge clk) init <= 1'b0;

  // RUN of lane K changes only at a frame end; while it is off lane K is idle
  logic run;
  logic [3:0] cnt_a, cnt_b;
  logic fe_a, fe_b;
  always_ff @(posedge clk)
    if (init) run <= 1'b0;
    else if (fe_a) run <= run_in;
  wire rk = run && req_k, dk = run && dreq_k;

  function automatic logic [1:0] put1(input logic [1:0] v, input logic b);
    put1 = v; put1[K] = b;
  endfunction
  function automatic logic [13:0] put7(input logic [13:0] v, input logic [6:0] b);
    put7 = v; put7[7*K +: 7] = b;
  endfunction
  function automatic logic [3:0] put2(input logic [3:0] v, input logic [1:0] b);
    put2 = v; put2[2*K +: 2] = b;
  endfunction
  function automatic logic [63:0] put32(input logic [63:0] v, input logic [31:0] b);
    put32 = v; put32[32*K +: 32] = b;
  endfunction

  logic [1:0]  g_a, g_b, dg_a, dg_b;
  logic [31:0] lr_a, lr_b, dr_a, dr_b, hr_a, hr_b;
  logic        hg_a, hg_b;

  tse_prog_mem #(.LANES(L), .LATCH(1'b0)) a (
    .clk, .rst_n(!init), .clr(clr_a), .all_stopped(1'b0),
    .l_req(put1(l_req_a, rk)), .l_addr(put7(l_addr_a, addr_k)), .l_win(put2(l_win_a, win_k)),
    .l_fb(put1(l_fb_a, fb_k)), .l_gnt(g_a), .l_rdata(lr_a),
    .d_req(put1(d_req_a, dk)), .d_we(put1(d_we_a, dwe_k)), .d_addr(put7(d_addr_a, daddr_k)),
    .d_wdata(put32(d_wdata_a, dwdata_k)), .d_gnt(dg_a), .d_rdata(dr_a),
    .h_req(h_req_a), .h_we(h_we_a), .h_dw(h_dw_a), .h_fb(h_fb_a), .h_addr(h_addr_a),
    .h_wdata(h_wdata_a), .h_gnt(hg_a), .h_rdata(hr_a),
    .t_en(t_en_a), .t_req(t_req_a), .t_we(t_we_a), .t_addr(t_addr_a), .t_wdata(t_wdata_a),
    .cnt(cnt_a), .frame_end(fe_a)
  );
  tse_prog_mem #(.LANES(L), .LATCH(1'b0)) b (
    .clk, .rst_n(!init), .clr(clr_b), .all_stopped(1'b0),
    .l_req(put1(l_req_b, rk)), .l_addr(put7(l_addr_b, addr_k)), .l_win(put2(l_win_b, win_k)),
    .l_fb(put1(l_fb_b, fb_k)), .l_gnt(g_b), .l_rdata(lr_b),
    .d_req(put1(d_req_b, dk)), .d_we(put1(d_we_b, dwe_k)), .d_addr(put7(d_addr_b, daddr_k)),
    .d_wdata(put32(d_wdata_b, dwdata_k)), .d_gnt(dg_b), .d_rdata(dr_b),
    .h_req(h_req_b), .h_we(h_we_b), .h_dw(h_dw_b), .h_fb(h_fb_b), .h_addr(h_addr_b),
    .h_wdata(h_wdata_b), .h_gnt(hg_b), .h_rdata(hr_b),
    .t_en(t_en_b), .t_req(t_req_b), .t_we(t_we_b), .t_addr(t_addr_b), .t_wdata(t_wdata_b),
    .cnt(cnt_b), .frame_end(fe_b)
  );

  // the fallback store of copy a, connected by the proof script
  logic [255:0] p_fb_q;

  // lane K's own count from RUN, a held request's wait, the last fallback grant
  logic [3:0] own;
  logic [2:0] waited;
  logic       fbg_q;
  logic [2:0] fba_q;
  always_ff @(posedge clk)
    if (init) begin
      own <= '0; waited <= '0; fbg_q <= 1'b0; fba_q <= '0;
    end else begin
      own    <= run ? own + 4'd1 : 4'd0;
      waited <= (!rk || g_a[K]) ? 3'd0 : waited + 3'd1;
      fbg_q  <= g_a[K] && fb_k;
      if (g_a[K]) fba_q <= addr_k[2:0];
    end

  always_comb if (!init) begin
`ifdef NI
    assert (g_a[K] == g_b[K]);
    assert (dg_a[K] == dg_b[K]);
    assert (cnt_a == cnt_b);
`else
    if (g_a[K]) assert (cnt_a[1:0] == 2'(K) && own[1:0] == 2'(K));
    if (dg_a[K]) assert (cnt_a == 4'(4 * K + 3) && own == 4'(4 * K + 3));
    if (run) assert (own == cnt_a);
    assert (waited <= 3'd3);
    if (fbg_q) assert (lr_a == p_fb_q[32 * fba_q +: 32]);
`endif
  end
  wire _unused = &{lr_a, fbg_q, fba_q, p_fb_q, lr_b, dr_a, dr_b, hr_a, hr_b, hg_a, hg_b, fe_b, dg_b, g_b, 1'b0};
endmodule
