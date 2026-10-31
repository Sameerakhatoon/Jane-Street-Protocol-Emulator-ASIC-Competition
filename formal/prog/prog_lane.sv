`default_nettype none
// Binds a compiled program to the real tse_lane for the compiler's
// per-program proofs. The compiler's harness instantiates a placeholder lane
// with (clk, rst, pin_in, pin_out, sample_strobe); this module is that
// placeholder built from the RTL:
//   program  PROG_HEX (256 lines, one 16-bit instruction each) as a constant,
//            two instructions per 32-bit word, read one cycle after the grant
//   fetch    granted in the lane's own slot, cycle mod 4 = LANE, counted from
//            the first running cycle (RUN takes effect at a frame start)
//   config   the 32 bank words are the constant CFG; the program must never
//            write one (asserted, so a program that does fails loudly)
//   host     TX mailbox writes are free; the host drains RXDATA at once
//   pins     pin_in is the level the lane sees (after the pad synchronizer),
//            pin_out the lane's pin register
//   sample   sample_strobe is high in the cycle after an instruction whose pc
//            is set in SAMPLE_PCS dispatched (its pc is left), so a sample
//            issued D cycles after the trigger shows up D + 1 cycles after it,
//            the latency count of the window checker
// The flow also writes the program as prog.hex, the default, because the
// tools elaborate the module with its defaults once. The pc comes from the
// lane's STATUS read port, so no hierarchical reference into the lane is
// needed.
module prog_lane #(
    parameter PROG_HEX = "prog.hex",
    parameter int LANE = 0,
    parameter logic [1023:0] CFG = '0,
    parameter logic [255:0] SAMPLE_PCS = '0
) (
    input  logic       clk,
    input  logic       rst,
    input  logic [3:0] pin_in,
    output logic [3:0] pin_out,
    output logic       sample_strobe
);
    logic [15:0] rom [256];
    initial begin
        $readmemh(PROG_HEX, rom);
    end

    (* anyseq *) logic        h_tx_we;
    (* anyseq *) logic [31:0] h_tx_data;
    (* anyseq *) logic [31:0] dmem_rdata;

    logic        rst_n, run;
    logic [3:0]  cyc;
    assign rst_n = !rst;
    assign run   = !rst;
    always_ff @(posedge clk)
        if (rst) cyc <= '0;
        else     cyc <= cyc + 4'd1;

    logic        imem_req, gnt, dmem_req, dmem_we, dgnt;
    logic [6:0]  imem_addr, dmem_addr;
    logic [31:0] imem_rdata, dmem_wdata;
    assign gnt  = run && imem_req && cyc[1:0] == 2'(LANE);
    assign dgnt = run && dmem_req && cyc == {2'(LANE), 2'd3};
    always_ff @(posedge clk)
        if (rst)      imem_rdata <= '0;
        else if (gnt) imem_rdata <= {rom[{imem_addr, 1'b1}], rom[{imem_addr, 1'b0}]};

    logic        cfg_we, h_flags_unused, ev_valid, halted, sig_we;
    logic [4:0]  cfg_word;
    logic [31:0] cfg_wdata, h_rdata, h_rx_data;
    logic [1:0]  h_flags;
    logic [5:0]  ev_code;
    logic [7:0]  ev_data;
    logic [3:0]  irq, sig_mask, sig_val, voe;
    tse_lane u_lane (
        .clk, .rst_n, .run, .cfg(CFG), .cfg_we, .cfg_word, .cfg_wdata,
        .h_we(1'b0), .h_re(1'b0), .h_addr(tse_regs_pkg::LR_STATUS), .h_wdata(32'd0), .h_rdata,
        .h_tx_we, .h_tx_data, .h_rx_re(1'b1), .h_rx_data, .h_flags,
        .ev_valid, .ev_code, .ev_data, .irq, .halted, .sig_we, .sig_mask, .sig_val,
        .imem_req, .imem_addr, .imem_gnt(gnt), .imem_rdata,
        .dmem_req, .dmem_we, .dmem_addr, .dmem_wdata, .dmem_gnt(dgnt), .dmem_rdata,
        .chip_flags(6'd0), .ts(32'd0),
        .vin_p(pin_in), .vin_n(pin_in), .vout(pin_out), .voe
    );

    logic [7:0] pc, pc_q;
    assign pc = h_rdata[tse_regs_pkg::LR_STATUS_PC_HI:tse_regs_pkg::LR_STATUS_PC_LO];
    always_ff @(posedge clk) pc_q <= rst ? 8'd0 : pc;
    assign sample_strobe = !rst && SAMPLE_PCS[pc_q] && pc != pc_q;

    always_comb if (!rst) assert (!cfg_we);
    assign h_flags_unused = &{cfg_word, cfg_wdata, h_rx_data, h_flags, ev_valid, ev_code, ev_data,
                              irq, halted, sig_we, sig_mask, sig_val, voe, dmem_we, dmem_wdata, 1'b0};
endmodule
`default_nettype wire
