// SPDX-License-Identifier: Apache-2.0
// Formal harness for tse_seq (one lane sequencer), immediate assertions only.
// Every input is free in every cycle: run, start_pc, grants, memory data,
// pins, events, flags, host mailbox and CSR read data.
//  FETCH:  the word buffer never hands the sequencer a wrong word. One word
//          address a and its content W are free constants, and the memory
//          returns W the cycle after a granted request for a; whenever the
//          sequencer dispatches from a, the word it decodes is W.
//  DATA:   a data window request that is not granted stays up, with the
//          same address, direction and write data, until it is granted or
//          the lane stops.
//  HALT:   a word tse_legal rejects halts the lane at its dispatch: the next
//          cycle shows halted and interrupt bit 3, the PC stays, and a halted
//          lane writes no pin, CSR, command, chip flag, event or data word.
//  STOP:   the cycle after the lane is stopped, pins and directions are 0,
//          C is 0, the PC is start_pc, halted is clear and no side effect
//          happens, whatever run does in that cycle.
module seq_props (
  input logic        clk,
  input logic        run,
  input logic [7:0]  start_pc,
  input logic        imem_gnt,
  input logic [31:0] imem_rdata,
  input logic        dmem_gnt,
  input logic [31:0] dmem_rdata,
  input logic [5:0]  alt_cfg,
  input logic [7:0]  flg,
  input logic [3:0]  pin_in,
  input logic        side_en,
  input logic [1:0]  ss_base,
  input logic [1:0]  jmp_pin,
  input logic [7:0]  ev,
  input logic [5:0]  chip_flags,
  input logic [63:0] gt,
  input logic [31:0] csr_rdata,
  input logic        h_tx_we,
  input logic [31:0] h_tx_data,
  input logic        h_rx_re
);
  logic init = 1'b1;
  always_ff @(posedge clk) init <= 1'b0;

  logic        imem_req, dmem_req, dmem_we, csr_we, csr_re, cmd_we, h_tx_full, h_rx_full;
  logic        sig_we, emit_v, timer_ack, halted, aflag_o, tflag_o;
  logic [6:0]  imem_addr, dmem_addr;
  logic [31:0] dmem_wdata, csr_wdata, h_rx_data;
  logic [3:0]  pin_out, pin_oe, pin_wr, irq, sig_mask, sig_val;
  logic [15:0] clock;
  logic [7:0]  pc_o, csr_addr, cmd, emit_data;
  logic [5:0]  emit_code;
  tse_seq dut (
    .clk, .rst_n(!init), .run, .start_pc, .imem_req, .imem_addr, .imem_gnt, .imem_rdata,
    .dmem_req, .dmem_we, .dmem_addr, .dmem_wdata, .dmem_gnt, .dmem_rdata,
    .alt_cfg, .flg, .pin_in, .pin_out, .pin_oe, .pin_wr, .side_en, .ss_base, .jmp_pin, .ev,
    .chip_flags, .gt, .clock, .pc_o, .csr_we, .csr_re, .csr_addr, .csr_wdata, .csr_rdata,
    .cmd_we, .cmd, .h_tx_we, .h_tx_data, .h_tx_full, .h_rx_re, .h_rx_data, .h_rx_full,
    .irq, .sig_we, .sig_mask, .sig_val, .emit_v, .emit_code, .emit_data, .timer_ack,
    .halted, .aflag_o, .tflag_o
  );

  // the sequencer's dispatch, its decoded word and its legality, connected by
  // the proof script (connect -set after flatten); kept where a property does
  // not read them, so every task connects the same probes
  (* keep *) logic        p_avail, p_legal;
  (* keep *) logic [31:0] p_word;

  // free memory word: address a holds W
  (* anyconst *) logic [6:0]  a;
  (* anyconst *) logic [31:0] W;
  logic       gq;
  logic [6:0] aq;
  always_ff @(posedge clk) begin
    gq <= !init && imem_req && imem_gnt;
    aq <= imem_addr;
  end
  always_comb if (gq && aq == a) assume (imem_rdata == W);

  // previous cycle
  logic        run_q, dreq_q, dgnt_q, dwe_q, halt_ev_q;
  logic [6:0]  daddr_q;
  logic [31:0] dwd_q;
  logic [7:0]  pc_q, spc_q;
  logic [3:0]  pins_q, dirs_q;
  always_ff @(posedge clk) begin
    run_q     <= init ? 1'b1 : run;
    dreq_q    <= !init && dmem_req;
    dgnt_q    <= dmem_gnt;
    dwe_q     <= dmem_we;
    daddr_q   <= dmem_addr;
    dwd_q     <= dmem_wdata;
    halt_ev_q <= !init && run && p_avail && !p_legal;
    pc_q      <= pc_o;
    spc_q     <= start_pc;
    pins_q    <= pin_out;
    dirs_q    <= pin_oe;
  end

  always_comb if (!init) begin
`ifdef FETCH
    if (p_avail && pc_o[7:1] == a) assert (p_word == W);
`endif
`ifdef DATA
    if (dreq_q && !dgnt_q && run_q && run)
      assert (dmem_req && dmem_addr == daddr_q && dmem_we == dwe_q && dmem_wdata == dwd_q);
`endif
`ifdef HALT
    if (halt_ev_q) assert (halted && irq == 4'b1000 && pc_o == pc_q);
    if (halted && run_q && run) begin
      assert (pc_o == pc_q && pin_out == pins_q && pin_oe == dirs_q);
      assert (!csr_we && !cmd_we && !sig_we && !dmem_req && pin_wr == 4'd0 && !emit_v);
    end
`endif
`ifdef STOP
    if (!run_q) begin
      assert (pin_out == 4'd0 && pin_oe == 4'd0 && clock == 16'd0 && pc_o == spc_q && !halted);
      assert (!csr_we && !cmd_we && !sig_we && !dmem_req && pin_wr == 4'd0);
    end
`endif
  end

  always @(posedge clk) if (!init) begin
    cover (p_avail && pc_o[7:1] == a && p_word == W && W != 32'd0);
    cover (dreq_q && !dgnt_q && dmem_req);
    cover (halted && run);
  end
endmodule
