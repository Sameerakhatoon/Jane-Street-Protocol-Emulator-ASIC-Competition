`default_nettype none
// Lockstep harness for one tse_seq: a 128-word program store written while
// stopped, the fetch grant of tse_prog_mem but with the slot counter aligned
// to RUN (cycle 0 = first cycle with run = 1, the cycle numbering of the
// ISA), the FLAG wiring of tse_lane, and a fixed CSR read stub that the
// Python side mirrors. Internal state is brought out for comparison.
module seq_harness (
  input  logic        clk,
  input  logic        rst_n,
  input  logic        run,
  input  logic [1:0]  lane,
  input  logic        prog_we,
  input  logic [6:0]  prog_addr,
  input  logic [31:0] prog_data,
  input  logic [5:0]  alt_cfg,
  input  logic        side_en,
  input  logic [2:0]  ss_base,
  input  logic [2:0]  jmp_pin,
  input  logic [3:0]  pins,
  input  logic [7:0]  ev,
  input  logic [3:0]  dpll,              // {SYNCED, EDGE, HALF, SAMPLE}
  input  logic        h_tx_we,
  input  logic [31:0] h_tx_data,
  input  logic        h_rx_re,
  output logic [7:0]  pin_out,
  output logic [7:0]  pin_oe,
  output logic        csr_we,
  output logic [7:0]  csr_addr,
  output logic [31:0] csr_wdata,
  output logic        cmd_we,
  output logic [7:0]  cmd,
  output logic        h_tx_full,
  output logic        h_rx_full,
  output logic [31:0] h_rx_data,
  output logic [3:0]  irq,
  output logic        aflag,
  output logic        tflag,
  output logic        dispatch,
  output logic [7:0]  pc,
  output logic [15:0] x,
  output logic [15:0] y,
  output logic [15:0] t,
  output logic [31:0] isr,
  output logic [31:0] osr
);
  logic [31:0] mem [128];
  logic [31:0] rdata, csr_rdata;
  logic [6:0]  imem_addr;
  logic        imem_req, gnt, csr_re;
  logic [1:0]  cyc;
  logic [7:0]  flg;
  logic [3:0]  pout, poe, pwr, sig_mask, sig_val;
  logic        sig_we, emit_v, timer_ack, halted, dmem_req, dmem_we;
  logic [6:0]  dmem_addr;
  logic [31:0] dmem_wdata;
  logic [15:0] clock;
  logic [5:0]  emit_code;
  logic [7:0]  emit_data;

  always_ff @(posedge clk) begin
    if (prog_we) mem[prog_addr] <= prog_data;
    if (gnt) rdata <= mem[imem_addr];
  end

  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n)   cyc <= '0;
    else if (run) cyc <= cyc + 2'd1;
    else          cyc <= '0;

  assign gnt       = run && imem_req && cyc == lane;
  assign flg       = {1'b1, dpll[3], !h_rx_full, h_tx_full, dpll[2:0], 1'b0};
  assign csr_rdata = {csr_addr, ~csr_addr, csr_addr ^ 8'h5a, 8'hc3};

  tse_seq u_seq (
    .clk, .rst_n, .run, .start_pc(8'd0), .imem_req, .imem_addr, .imem_gnt(gnt), .imem_rdata(rdata),
    .dmem_req, .dmem_we, .dmem_addr, .dmem_wdata, .dmem_gnt(1'b0), .dmem_rdata(32'd0),
    .alt_cfg, .flg, .pin_in(pins), .pin_out(pout), .pin_oe(poe), .pin_wr(pwr), .side_en, .ss_base(ss_base[1:0]),
    .jmp_pin(jmp_pin[1:0]), .ev, .chip_flags(6'd0), .gt(64'd0), .clock, .pc_o(pc),
    .csr_we, .csr_re, .csr_addr, .csr_wdata, .csr_rdata, .cmd_we, .cmd,
    .h_tx_we, .h_tx_data, .h_tx_full, .h_rx_re, .h_rx_data, .h_rx_full, .irq,
    .sig_we, .sig_mask, .sig_val, .emit_v, .emit_code, .emit_data, .timer_ack, .halted,
    .aflag_o(aflag), .tflag_o(tflag)
  );

  assign pin_out  = {4'd0, pout};
  assign pin_oe   = {4'd0, poe};
  assign dispatch = u_seq.avail && !u_seq.held;
  assign x   = u_seq.x;
  assign y   = u_seq.y;
  assign t   = u_seq.t;
  assign isr = u_seq.isr;
  assign osr = u_seq.osr;
  wire _unused = &{csr_re, pwr, clock, timer_ack, halted, dmem_req, dmem_we, dmem_addr, dmem_wdata, sig_we, sig_mask, sig_val, emit_v, emit_code, emit_data, ss_base[2], jmp_pin[2], 1'b0};
endmodule
`default_nettype wire
