`default_nettype none
// Properties on the shared-state product (fp_core, built by product.py from
// the flattened Tiny Tapeout top). Copies a and b hold the same state and
// see the same pins; only the SRAM read data of the current cycle differs.
//   SDC:    every SRAM input the macro samples at the next edge is the same
//           in both copies: MEN always, WEN, REN and ADDR when MEN is 1, DIN
//           when MEN and WEN are 1. So no value travelling from A_DOUT
//           through logic to those pins in a cycle can change what the
//           memory does at the end of that cycle.
//   STRICT: the five inputs are equal in every cycle, used or not.
// The state starts anywhere (no reset is assumed) and the checks start
// after the first rising edge, so they cover every state the chip can be in
// one edge after any state at all, which includes every state after a
// reset. Both clock edges are modelled (the crossbar samples pins on the
// falling edge), and the checks hold after each.
module fp_props (
    input wire       clk,
    input wire       rst_n,
    input wire       ena,
    input wire [7:0] ui_in,
    input wire [7:0] uio_in
);
    logic        a_men, a_wen, a_ren, b_men, b_wen, b_ren;
    logic [8:0]  a_addr, b_addr;
    logic [31:0] a_din, b_din;

    fp_core u_core (
        .clk, .rst_n, .ena, .ui_in, .uio_in,
        .a_men, .a_wen, .a_ren, .a_addr, .a_din,
        .b_men, .b_wen, .b_ren, .b_addr, .b_din
    );

    // multiclock: one solver step per clock edge; the clock toggles every step
    logic clk_q;
    always @($global_clock) clk_q <= clk;
    always_comb assume (clk != clk_q);

    logic init = 1'b1;
    always_ff @(posedge clk) init <= 1'b0;

    always_comb
        if (!init) begin
`ifdef STRICT
            assert (a_men == b_men && a_wen == b_wen && a_ren == b_ren
                    && a_addr == b_addr && a_din == b_din);
`else
            assert (a_men == b_men);
            if (a_men) assert (a_wen == b_wen && a_ren == b_ren && a_addr == b_addr);
            if (a_men && a_wen) assert (a_din == b_din);
`endif
        end
endmodule
`default_nettype wire
