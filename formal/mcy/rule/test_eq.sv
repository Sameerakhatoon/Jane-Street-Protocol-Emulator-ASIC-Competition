// MCY miter: the original tse_resp_rule (mutsel = 0) against the mutant
// (mutsel = 1), same inputs, every output. The configuration is free and
// constant during a run, as the kill test has it.
module miter (
  input logic clk,
  input logic frame_rst, bit_valid, bit_in, bit_start, pin_clk, pin_dat, crc_ok, ext_flag,
  input logic seq_wr
);
  (* anyconst *) logic [31:0] pat, msk, resp;
  (* anyconst *) logic [17:0] pos;
  (* anyconst *) logic [27:0] ctl;
  (* anyconst *) logic        src, edge_sel, hold;
  (* anyconst *) logic [7:0]  tdly;
  logic init = 1'b1;
  always_ff @(posedge clk) init <= 1'b0;
  logic [5:0] r, u;
  tse_resp_rule ref_i (
    .mutsel(1'b0), .clk, .rst_n(!init), .cfg_pat(pat), .cfg_msk(msk), .cfg_pos(pos),
    .cfg_resp(resp), .cfg_ctl(ctl), .cfg_src(src), .cfg_edge(edge_sel), .cfg_tdly(tdly),
    .cfg_hold(hold), .seq_wr, .frame_rst, .bit_valid, .bit_in, .bit_start, .pin_clk, .pin_dat,
    .crc_ok, .ext_flag, .flags_clr(frame_rst), .drv_en(r[0]), .drv_val(r[1]),
    .hit_flags(r[3:2]), .hit_pulse(r[4]), .abort_pulse(r[5]));
  tse_resp_rule uut_i (
    .mutsel(1'b1), .clk, .rst_n(!init), .cfg_pat(pat), .cfg_msk(msk), .cfg_pos(pos),
    .cfg_resp(resp), .cfg_ctl(ctl), .cfg_src(src), .cfg_edge(edge_sel), .cfg_tdly(tdly),
    .cfg_hold(hold), .seq_wr, .frame_rst, .bit_valid, .bit_in, .bit_start, .pin_clk, .pin_dat,
    .crc_ok, .ext_flag, .flags_clr(frame_rst), .drv_en(u[0]), .drv_val(u[1]),
    .hit_flags(u[3:2]), .hit_pulse(u[4]), .abort_pulse(u[5]));
  always_comb if (!init) assert (r == u);
endmodule
