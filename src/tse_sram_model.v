// Behavioural stand-in that follows the PDK functional model: registered
// read data that holds until the next read, bit-masked writes, write-through.
// Contents start as X, like the silicon starts random.
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
    assign A_DOUT = dr;
    always @(posedge A_CLK)
        if (A_MEN && A_WEN) begin
            mem[A_ADDR] <= (mem[A_ADDR] & ~A_BM) | (A_DIN & A_BM);
            if (A_REN) dr <= (mem[A_ADDR] & ~A_BM) | (A_DIN & A_BM);
        end else if (A_MEN && A_REN) dr <= mem[A_ADDR];
`ifdef SRAM_FAULT
    always @(posedge A_CLK) mem[77][5] <= 1'b1;      // stuck-at-1 cell, to see the March test fail
`endif
    wire _unused = &{A_DLY, A_BIST_CLK, A_BIST_EN, A_BIST_MEN, A_BIST_WEN, A_BIST_REN,
                                     A_BIST_ADDR, A_BIST_DIN, A_BIST_BM, 1'b0};
endmodule
/* verilator lint_on DECLFILENAME */
`endif
