// Formal harness for tse_slot_arb, immediate assertions only.
//  NI:    two copies of the arbiter share the frame sync and lane K's
//         requests; every other lane and the host request anything at all,
//         independently in each copy. Lane K's fetch and data grants, its
//         read-valid strobes and the memory address in its granted cycles are
//         the same in both copies, so no other requester changes lane K's
//         timing (2-safety by self-composition).
//  OWN:   fixed slot ownership in copy a: lane i is granted a fetch only
//         when cnt mod 4 = i, a data access only when cnt = 4i + 3; the host
//         only in its own cycle (cnt = 15 with 3 lanes) unless every lane is
//         stopped; at most one requester uses the memory in a cycle; a held
//         fetch request is granted within 4 cycles.
// all_stopped is 0: lane K is running, so not every lane is stopped.
module arb_props #(
  parameter int LANES = 3,
  parameter int K = 1
) (
  input logic                clk,
  input logic                frame_sync,
  input logic                req_k,
  input logic [8:0]          addr_k,
  input logic                dreq_k,
  input logic                dwe_k,
  input logic [6:0]          daddr_k,
  input logic [31:0]         dwdata_k,
  input logic [LANES-1:0]    req_a, req_b, dreq_a, dreq_b, dwe_a, dwe_b,
  input logic [LANES*9-1:0]  addr_a, addr_b,
  input logic [LANES*7-1:0]  daddr_a, daddr_b,
  input logic                sys_req_a, sys_req_b, sys_we_a, sys_we_b,
  input logic [8:0]          sys_addr_a, sys_addr_b
);
  logic init = 1'b1;
  always_ff @(posedge clk) init <= 1'b0;

  function automatic logic [LANES-1:0] with_k(input logic [LANES-1:0] v, input logic b);
    with_k = v;
    with_k[K] = b;
  endfunction

  wire [LANES-1:0] ra = with_k(req_a, req_k), rb = with_k(req_b, req_k);
  wire [LANES-1:0] da = with_k(dreq_a, dreq_k), db = with_k(dreq_b, dreq_k);
  wire [LANES-1:0] wa = with_k(dwe_a, dwe_k), wb = with_k(dwe_b, dwe_k);
  logic [LANES*9-1:0] aa, ab;
  logic [LANES*7-1:0] xa, xb;
  logic [LANES*32-1:0] dw;
  always_comb begin
    aa = addr_a; ab = addr_b; xa = daddr_a; xb = daddr_b; dw = '0;
    aa[9*K +: 9] = addr_k; ab[9*K +: 9] = addr_k;
    xa[7*K +: 7] = daddr_k; xb[7*K +: 7] = daddr_k;
    dw[32*K +: 32] = dwdata_k;
  end

  logic [3:0]       cnt_a, cnt_b;
  logic [LANES-1:0] g_a, g_b, rv_a, rv_b, dg_a, dg_b, drv_a, drv_b;
  logic             sg_a, sg_b, srv_a, srv_b, men_a, men_b, wen_a, wen_b, ren_a, ren_b;
  logic [8:0]       ma_a, ma_b;
  logic [31:0]      din_a, din_b;
  tse_slot_arb #(.LANES(LANES)) u_a (
    .clk, .rst_n(!init), .frame_sync, .req(ra), .addr(aa), .dreq(da), .dwe(wa), .daddr(xa),
    .dwdata(dw), .sys_req(sys_req_a), .sys_we(sys_we_a), .sys_addr(sys_addr_a), .sys_wdata(32'd0),
    .all_stopped(1'b0), .cnt(cnt_a), .gnt(g_a), .rvalid(rv_a), .dgnt(dg_a), .drvalid(drv_a),
    .sys_gnt(sg_a), .sys_rvalid(srv_a), .mem_men(men_a), .mem_wen(wen_a), .mem_ren(ren_a),
    .mem_addr(ma_a), .mem_din(din_a));
  tse_slot_arb #(.LANES(LANES)) u_b (
    .clk, .rst_n(!init), .frame_sync, .req(rb), .addr(ab), .dreq(db), .dwe(wb), .daddr(xb),
    .dwdata(dw), .sys_req(sys_req_b), .sys_we(sys_we_b), .sys_addr(sys_addr_b), .sys_wdata(32'd0),
    .all_stopped(1'b0), .cnt(cnt_b), .gnt(g_b), .rvalid(rv_b), .dgnt(dg_b), .drvalid(drv_b),
    .sys_gnt(sg_b), .sys_rvalid(srv_b), .mem_men(men_b), .mem_wen(wen_b), .mem_ren(ren_b),
    .mem_addr(ma_b), .mem_din(din_b));

  // bounded wait: lane K holds a fetch request until it is granted
  logic       held;
  logic [2:0] waited;
  always_ff @(posedge clk) begin
    held <= !init && req_k && !g_a[K];
    if (init || !req_k || g_a[K]) waited <= 3'd0;
    else waited <= waited + 3'd1;
  end
  always_comb if (held) assume (req_k && !frame_sync);

  logic [3:0] users;
  always_comb begin
    users = 4'(sg_a);
    for (int i = 0; i < LANES; i++) users = users + 4'(g_a[i]) + 4'(dg_a[i]);
  end

  always_comb if (!init) begin
`ifdef NI
    assert (cnt_a == cnt_b);
    assert (g_a[K] == g_b[K] && rv_a[K] == rv_b[K]);
    assert (dg_a[K] == dg_b[K] && drv_a[K] == drv_b[K]);
    if (g_a[K] || dg_a[K]) assert (ma_a == ma_b && wen_a == wen_b && din_a == din_b);
`else
    for (int i = 0; i < LANES; i++) begin
      if (g_a[i]) assert (cnt_a[1:0] == 2'(i));
      if (dg_a[i]) assert (LANES < 4 && cnt_a == 4'(4 * i + 3));
    end
    if (sg_a) assert (LANES < 4 && cnt_a == 4'd15);
    assert (users <= 1);
    assert (waited < 3'd4);
`endif
  end
  wire _unused = &{srv_a, srv_b, men_a, men_b, ren_a, ren_b, cnt_b, g_b, rv_b, dg_b, drv_b, sg_b,
                   ma_b, wen_b, din_b, 1'b0};
endmodule
