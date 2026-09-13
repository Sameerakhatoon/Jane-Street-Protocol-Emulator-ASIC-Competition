`default_nettype none
// Programmable bit-serial CRC, width 1..32 (its constants live
// in the configuration bank). Left-aligned: the feedback tap is
// always bit 31, so software writes poly, init, xo and check shifted left by
// 32 - W. xo is the xorout mask in transmission order.
module tse_crc #(
    parameter bit RIGHT_ALIGN = 1
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [31:0] cfg_poly,
    input  logic [31:0] cfg_init,
    input  logic [31:0] cfg_xo,
    input  logic [31:0] cfg_check,
    input  logic [4:0]  cfg_wm1,       // width minus one
    input  logic        cfg_refin,
    input  logic        cfg_refout,
    input  logic        clear,
    input  logic        bit_valid,
    input  logic        bit_in,
    input  logic        byte_valid,
    input  logic [7:0]  byte_in,
    output logic        busy,
    input  logic        tx_load,
    input  logic        tx_shift,
    output logic        tx_bit,
    output logic [31:0] result,
    output logic        check_ok
);
    logic [31:0] crc, t;
    logic [7:0]  bsr;
    logic [3:0]  bcnt;
    logic        din, fb;

    function automatic logic [31:0] rev32(input logic [31:0] v);
        for (int i = 0; i < 32; i++) rev32[i] = v[31-i];
    endfunction

    assign busy     = bcnt != 4'd0;
    assign din      = busy ? (cfg_refin ? bsr[0] : bsr[7]) : bit_in;
    assign fb       = crc[31] ^ din;
    assign t        = crc ^ cfg_xo;
    assign tx_bit   = crc[31];
    assign check_ok = t == cfg_check;
    assign result   = cfg_refout ? rev32(t) : RIGHT_ALIGN ? (t >> (5'd31 - cfg_wm1)) : t;

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n)                 crc <= '0;
        else if (clear)             crc <= cfg_init;
        else if (tx_load)           crc <= t;
        else if (tx_shift)          crc <= {crc[30:0], 1'b0};
        else if (bit_valid || busy) crc <= {crc[30:0], 1'b0} ^ (fb ? cfg_poly : 32'd0);

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            bsr <= '0; bcnt <= '0;
        end else if (byte_valid && !busy) begin
            bsr <= byte_in; bcnt <= 4'd8;
        end else if (busy) begin
            bsr  <= cfg_refin ? {1'b0, bsr[7:1]} : {bsr[6:0], 1'b0};
            bcnt <= bcnt - 4'd1;
        end
endmodule
`default_nettype wire
