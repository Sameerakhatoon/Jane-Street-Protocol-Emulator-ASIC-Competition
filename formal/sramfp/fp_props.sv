`default_nettype none
// Properties on the shared-state product (fp_core, built by product.py from
// the flattened Tiny Tapeout top). Copies a and b hold the same state and
// see the same pins; only the SRAM read data of the current cycle differs
// (both macros, lo and hi, read free values in each copy).
//   SDC:    for each macro, every input it samples at the next edge is the
//           same in both copies: MEN always, WEN, REN and ADDR when its MEN
//           is 1, DIN when its MEN and WEN are 1. So no value travelling from
//           A_DOUT through logic to those pins in a cycle can change what
//           the memory does at the end of that cycle.
//   STRICT: the five inputs of each macro are equal in every cycle.
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
    logic        a_lo_men, a_lo_wen, a_lo_ren, b_lo_men, b_lo_wen, b_lo_ren;
    logic        a_hi_men, a_hi_wen, a_hi_ren, b_hi_men, b_hi_wen, b_hi_ren;
    logic [8:0]  a_lo_addr, b_lo_addr, a_hi_addr, b_hi_addr;
    logic [15:0] a_lo_din, b_lo_din, a_hi_din, b_hi_din;

    fp_core u_core (
        .clk, .rst_n, .ena, .ui_in, .uio_in,
        .a_lo_men, .a_lo_wen, .a_lo_ren, .a_lo_addr, .a_lo_din,
        .a_hi_men, .a_hi_wen, .a_hi_ren, .a_hi_addr, .a_hi_din,
        .b_lo_men, .b_lo_wen, .b_lo_ren, .b_lo_addr, .b_lo_din,
        .b_hi_men, .b_hi_wen, .b_hi_ren, .b_hi_addr, .b_hi_din
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
            assert (a_lo_men == b_lo_men && a_lo_wen == b_lo_wen && a_lo_ren == b_lo_ren
                    && a_lo_addr == b_lo_addr && a_lo_din == b_lo_din);
            assert (a_hi_men == b_hi_men && a_hi_wen == b_hi_wen && a_hi_ren == b_hi_ren
                    && a_hi_addr == b_hi_addr && a_hi_din == b_hi_din);
`else
            assert (a_lo_men == b_lo_men);
            if (a_lo_men) assert (a_lo_wen == b_lo_wen && a_lo_ren == b_lo_ren && a_lo_addr == b_lo_addr);
            if (a_lo_men && a_lo_wen) assert (a_lo_din == b_lo_din);
            assert (a_hi_men == b_hi_men);
            if (a_hi_men) assert (a_hi_wen == b_hi_wen && a_hi_ren == b_hi_ren && a_hi_addr == b_hi_addr);
            if (a_hi_men && a_hi_wen) assert (a_hi_din == b_hi_din);
`endif
        end
endmodule
`default_nettype wire
