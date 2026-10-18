`default_nettype none
// Lockstep harness for one tse_lane: a 128-word program store written while
// stopped, the fetch grant with the slot counter aligned to RUN (cycle 0 is
// the first cycle with run = 1), the lane's configuration bank written only
// by the lane itself (programs configure the lane with CSRW, so the harness
// never needs the bank layout; cfg_clr empties it, as BEGIN does), the data
// window served in the lane's data slot, the host mailboxes and the host
// read port. Assists are idle unless a program starts them; chip flags are
// tied to 0 and the timestamp counts from RUN, which is what the model assumes.
module lane_harness (
  input  logic        clk,
  input  logic        rst_n,
  input  logic        run,
  input  logic        cfg_clr,
  input  logic [1:0]  lane,
  input  logic        prog_we,
  input  logic [6:0]  prog_addr,
  input  logic [31:0] prog_data,
  input  logic        dwin_we,
  input  logic [6:0]  dwin_addr,
  input  logic [31:0] dwin_data,
  input  logic [3:0]  pins,
  input  logic        h_re,
  input  logic [7:0]  h_addr,
  input  logic        h_tx_we,
  input  logic [31:0] h_tx_data,
  input  logic        h_rx_re,
  output logic [31:0] h_rdata,
  output logic [31:0] h_rx_data,
  output logic [1:0]  h_flags,
  output logic [3:0]  vout,
  output logic [3:0]  voe,
  output logic [3:0]  irq,
  output logic        dispatch,
  output logic [7:0]  pc,
  output logic [15:0] x,
  output logic [15:0] y,
  output logic [15:0] t,
  output logic [31:0] isr,
  output logic [31:0] osr,
  output logic        aflag,
  output logic        tflag
);
  logic [31:0] mem [128];
  logic [31:0] rdata;
  logic [6:0]  imem_addr;
  logic        imem_req, gnt;
  logic [3:0]  cyc;
  logic [31:0] ts;

  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n)   begin cyc <= '0; ts <= '0; end
    else if (run) begin cyc <= cyc + 4'd1; ts <= ts + 32'd1; end
    else          begin cyc <= '0; ts <= '0; end

  assign gnt = run && imem_req && cyc[1:0] == lane;
  always_ff @(posedge clk) begin
    if (prog_we) mem[prog_addr] <= prog_data;
    if (gnt) rdata <= mem[imem_addr];
  end

  logic [1023:0] cfg;
  logic          cfg_we;
  logic [4:0]    cfg_word;
  logic [31:0]   cfg_wdata, cfg_rd;
  logic          cfg_busy;
  tse_cfg_bank #(.WORDS(32), .LATCH(1'b1)) u_bank (
    .clk, .rst_n, .clr(cfg_clr), .we(cfg_we), .addr(cfg_word), .wdata(cfg_wdata), .busy(cfg_busy),
    .rd_addr(5'd0), .rd_data(cfg_rd), .q(cfg)
  );

  // data window: the access happens in the grant cycle s (cycle mod 16 =
  // 4 lane + 3), the LDW word is on dmem_rdata in s + 1
  logic [31:0] dwin [128];
  logic [31:0] drdata, dmem_wdata;
  logic [6:0]  dmem_addr;
  logic        dmem_req, dmem_we, dgnt;
  assign dgnt = run && dmem_req && cyc == {lane, 2'd3};
  always_ff @(posedge clk) begin
    if (dwin_we) dwin[dwin_addr] <= dwin_data;
    if (dgnt && dmem_we) dwin[dmem_addr] <= dmem_wdata;
    if (dgnt && !dmem_we) drdata <= dwin[dmem_addr];
  end

  logic       ev_valid, halted, sig_we;
  logic [5:0] ev_code;
  logic [7:0] ev_data;
  logic [3:0] sig_mask, sig_val;
  tse_lane u_lane (
    .clk, .rst_n, .run, .cfg, .cfg_we, .cfg_word, .cfg_wdata,
    .h_we(1'b0), .h_re, .h_addr, .h_wdata(32'd0), .h_rdata,
    .h_tx_we, .h_tx_data, .h_rx_re, .h_rx_data, .h_flags,
    .ev_valid, .ev_code, .ev_data, .irq, .halted, .sig_we, .sig_mask, .sig_val,
    .imem_req, .imem_addr, .imem_gnt(gnt), .imem_rdata(rdata),
    .dmem_req, .dmem_we, .dmem_addr, .dmem_wdata, .dmem_gnt(dgnt), .dmem_rdata(drdata),
    .chip_flags(6'd0), .ts,
    .vin_p(pins), .vin_n(pins), .vout, .voe
  );

  assign dispatch = u_lane.u_seq.avail && !u_lane.u_seq.held;
  assign pc    = u_lane.u_seq.pc;
  assign x     = u_lane.u_seq.x;
  assign y     = u_lane.u_seq.y;
  assign t     = u_lane.u_seq.t;
  assign isr   = u_lane.u_seq.isr;
  assign osr   = u_lane.u_seq.osr;
  assign aflag = u_lane.u_seq.aflag_o;
  assign tflag = u_lane.u_seq.tflag_o;
  wire _unused = &{cfg_rd, cfg_busy, ev_valid, ev_code, ev_data, halted, sig_we, sig_mask, sig_val, 1'b0};
endmodule
`default_nettype wire
