// Behavioural stand-in that follows the PDK functional model: registered
// read data that holds until the next read, bit-masked writes, write-through,
// and A_BIST_EN selecting the test port for every input including the clock.
// Contents start as X, like the silicon starts random.
// Fault injection for the self-test: a test sets fault_kind (and optionally
// fault_addr, fault_aggr, fault_bit) through the hierarchy; -DSRAM_FAULT
// starts with a stuck-at-1 cell at word 77 bit 5.
//   1 stuck-at-1      victim bit reads 1
//   2 stuck-at-0      victim bit reads 0
//   3 transition      victim bit cannot rise from 0 to 1
//   4 coupling        a rising write of bit fault_bit at fault_aggr inverts the victim
//   5 address alias   fault_aggr decodes to the victim word
//   6 dead macro      every read returns 0
// guard_errors counts accesses next to an A_BIST_EN change, which the
// datasheet forbids one cycle before and after; the check is one cycle
// stricter (the cycle the new value is first sampled, and the one after).
// The 512x32 model is the golden reference the simulation wraps as u_macro; the
// 512x16 model below serves the gate-level netlist, where the store is built
// from two 512x16 macros (see tse_sram_512x32.sv).
`ifdef SIM
/* verilator lint_off DECLFILENAME */
module RM_IHPSG13_1P_512x32_c2_bm_bist (
    input         A_CLK, A_MEN, A_WEN, A_REN,
    input  [8:0]  A_ADDR,
    input  [31:0] A_DIN,
    input         A_DLY,
    output [31:0] A_DOUT,
    input  [31:0] A_BM,
    input         A_BIST_CLK, A_BIST_EN, A_BIST_MEN, A_BIST_WEN, A_BIST_REN,
    input  [8:0]  A_BIST_ADDR,
    input  [31:0] A_BIST_DIN, A_BIST_BM
);
    reg [31:0] mem [0:511];
    reg [31:0] dr;
    reg [2:0]  fault_kind;
    reg [8:0]  fault_addr, fault_aggr;
    reg [4:0]  fault_bit;
    integer    guard_errors;

    initial begin
`ifdef SRAM_FAULT
        fault_kind = 3'd1;
`else
        fault_kind = 3'd0;
`endif
        fault_addr = 9'd77; fault_aggr = 9'd78; fault_bit = 5'd5;
        guard_errors = 0;
    end

    /* verilator lint_off SYNCASYNCNET */
    wire        clk_m = A_BIST_EN ? A_BIST_CLK  : A_CLK;
    /* verilator lint_on SYNCASYNCNET */
    wire        men   = A_BIST_EN ? A_BIST_MEN  : A_MEN;
    wire        wen   = A_BIST_EN ? A_BIST_WEN  : A_WEN;
    wire        ren   = A_BIST_EN ? A_BIST_REN  : A_REN;
    wire [8:0]  araw  = A_BIST_EN ? A_BIST_ADDR : A_ADDR;
    wire [31:0] din   = A_BIST_EN ? A_BIST_DIN  : A_DIN;
    wire [31:0] bm    = A_BIST_EN ? A_BIST_BM   : A_BM;
    wire [8:0]  addr  = (fault_kind == 3'd5 && araw == fault_aggr) ? fault_addr : araw;
    wire [31:0] fbit  = 32'd1 << fault_bit;

    function [31:0] seen(input [8:0] a, input [31:0] v);
        begin
            seen = v;
            if (a == fault_addr && fault_kind == 3'd1) seen = v | fbit;
            if (a == fault_addr && fault_kind == 3'd2) seen = v & ~fbit;
            if (fault_kind == 3'd6) seen = 32'd0;
        end
    endfunction

    wire [31:0] nv0 = (mem[addr] & ~bm) | (din & bm);
    wire        rise = !mem[addr][fault_bit] && nv0[fault_bit];
    wire [31:0] nv  = (fault_kind == 3'd3 && addr == fault_addr && rise) ? nv0 & ~fbit : nv0;
    always @(posedge clk_m)
        if (men && wen) begin
            mem[addr] <= nv;
            if (fault_kind == 3'd4 && addr == fault_aggr && rise)
                mem[fault_addr][fault_bit] <= ~mem[fault_addr][fault_bit];
            if (ren) dr <= seen(addr, nv);
        end else if (men && ren) dr <= seen(addr, mem[addr]);

    assign A_DOUT = dr;

    reg en_q, acc_q, chg_q;
    initial begin en_q = 1'b0; acc_q = 1'b0; chg_q = 1'b0; end
    wire acc = A_MEN || A_BIST_MEN;
    wire chg = A_BIST_EN !== en_q;
    always @(posedge A_CLK) begin
        if ((chg && (acc_q || acc)) || (chg_q && acc)) guard_errors <= guard_errors + 1;
        chg_q <= chg;
        en_q  <= A_BIST_EN;
        acc_q <= acc;
    end

    wire _unused = &{A_DLY, 1'b0};
endmodule

// Functional 512x16 half used by the gate-level netlist (two halves make the
// 32-bit word). Registered read that holds, bit-masked write-through, and
// A_BIST_EN selecting the test port. No fault injection: the gate-level test
// does not inject faults, and the RTL simulation uses the 512x32 model above.
module RM_IHPSG13_1P_512x16_c2_bm_bist (
    input         A_CLK, A_MEN, A_WEN, A_REN,
    input  [8:0]  A_ADDR,
    input  [15:0] A_DIN,
    input         A_DLY,
    output [15:0] A_DOUT,
    input  [15:0] A_BM,
    input         A_BIST_CLK, A_BIST_EN, A_BIST_MEN, A_BIST_WEN, A_BIST_REN,
    input  [8:0]  A_BIST_ADDR,
    input  [15:0] A_BIST_DIN, A_BIST_BM
);
    reg [15:0] mem [0:511];
    reg [15:0] dr;

    /* verilator lint_off SYNCASYNCNET */
    wire        clk_m = A_BIST_EN ? A_BIST_CLK  : A_CLK;
    /* verilator lint_on SYNCASYNCNET */
    wire        men   = A_BIST_EN ? A_BIST_MEN  : A_MEN;
    wire        wen   = A_BIST_EN ? A_BIST_WEN  : A_WEN;
    wire        ren   = A_BIST_EN ? A_BIST_REN  : A_REN;
    wire [8:0]  addr  = A_BIST_EN ? A_BIST_ADDR : A_ADDR;
    wire [15:0] din   = A_BIST_EN ? A_BIST_DIN  : A_DIN;
    wire [15:0] bm    = A_BIST_EN ? A_BIST_BM   : A_BM;

    wire [15:0] nv = (mem[addr] & ~bm) | (din & bm);
    always @(posedge clk_m)
        if (men && wen) begin
            mem[addr] <= nv;
            if (ren) dr <= nv;
        end else if (men && ren) dr <= mem[addr];

    assign A_DOUT = dr;

    wire _unused = &{A_DLY, 1'b0};
endmodule
/* verilator lint_on DECLFILENAME */
`endif
