// SPDX-License-Identifier: Apache-2.0
// Formal harness for tse_crc, immediate assertions only.
//  EQUIV: for every width 5..32, polynomial, initial value and input stream
//         (single bits or whole bytes in either bit order) the left-aligned
//         CRC register, read through the reflected output, equals the
//         textbook MSB-first register shifted left. The unreflected
//         output is covered by the CRC-15/CAN catalogue task.
//  CAT:   with a catalogue configuration, feeding "123456789" as bytes gives
//         the catalogue check value on result (left-aligned when unreflected).
module crc_props (
  input logic       clk,
  input logic       clear_in,
  input logic       bit_valid_in,
  input logic       bit_in,
  input logic       byte_valid_in,
  input logic [7:0] byte_free
);
  logic [1:0] age = 2'd0;                   // 0: reset, 1: clear, 2: running
  logic        busy, check_ok, tx_bit;
  logic [31:0] result;
  always_ff @(posedge clk) if (age != 2'd2) age <= age + 2'd1;
  wire started = age == 2'd2;

`ifdef CAT
`ifdef CRC5_USB
  localparam logic [5:0] W = 6'd5;  localparam logic [31:0] P = 32'h05, I = 32'h1f, X = 32'h1f, C = 32'h19;
  localparam logic R = 1'b1;
`elsif CRC15_CAN
  localparam logic [5:0] W = 6'd15; localparam logic [31:0] P = 32'h4599, I = 32'h0, X = 32'h0, C = 32'h059e;
  localparam logic R = 1'b0;
`elsif CRC16_USB
  localparam logic [5:0] W = 6'd16; localparam logic [31:0] P = 32'h8005, I = 32'hffff, X = 32'hffff, C = 32'hb4c8;
  localparam logic R = 1'b1;
`else
  localparam logic [5:0] W = 6'd32; localparam logic [31:0] P = 32'h04c11db7, I = 32'hffffffff, X = 32'hffffffff, C = 32'hcbf43926;
  localparam logic R = 1'b1;
`endif
  wire [5:0]  w = W;
  wire [31:0] poly = P, ini = I, xo = X;
  wire        refin = R, refout = R;
  localparam logic [71:0] MSG = "123456789";
  logic [3:0] nb = 4'd0;                       // bytes sent
  logic       gap = 1'b0;
  wire        clear = age == 2'd1;
  wire        bit_valid = 1'b0;
  wire        byte_valid = started && !busy && !gap && nb != 4'd9;
  wire [7:0]  byte_in = MSG[8 * (4'd8 - nb) +: 8];
  always_ff @(posedge clk) begin
    gap <= byte_valid;
    if (byte_valid) nb <= nb + 4'd1;
  end
  wire _unused_in = &{clear_in, bit_valid_in, byte_valid_in, byte_free, 1'b0};
`else
  (* anyconst *) logic [5:0]  w;
  (* anyconst *) logic [31:0] poly, ini;
  (* anyconst *) logic        refin;
  wire [31:0] xo = 32'd0;
  wire        refout = 1'b1;               // result then shows the whole register
  always_comb assume (w >= 6'd5 && w <= 6'd32);
  wire       clear = age == 2'd1 || (started && clear_in);
  always_comb if (!started) assume (!bit_valid_in && !byte_valid_in);
  wire       bit_valid = bit_valid_in;
  wire       byte_valid = byte_valid_in;
  wire [7:0] byte_in = byte_free;
  always_comb if (hcnt != 4'd0) assume (!bit_valid && !byte_valid && !clear_in);
  always_comb assume (!(bit_valid && byte_valid));
`endif

  wire [5:0]  sh   = 6'd32 - w;
  wire [31:0] mask = (w == 6'd32) ? 32'hffff_ffff : ((32'd1 << w) - 32'd1);
  wire [31:0] p    = poly & mask;

  wire [5:0] wm1_6 = w - 6'd1;
  wire [4:0] wm1   = wm1_6[4:0];
  tse_crc dut (
    .clk, .rst_n(age != 2'd0), .cfg_poly(p << sh), .cfg_init((ini & mask) << sh), .cfg_xo((xo & mask) << sh),
    .cfg_check(32'd0), .cfg_wm1(wm1), .cfg_refin(refin), .cfg_refout(refout),
    .clear, .bit_valid, .bit_in, .byte_valid, .byte_in, .busy,
    .tx_load(1'b0), .tx_shift(1'b0), .tx_bit, .result, .check_ok
  );

  // the textbook register: width w, MSB first, never reflected
  function automatic logic [31:0] step(input logic [31:0] s, input logic b, input logic [31:0] pp,
                                       input logic [31:0] m, input logic [4:0] top);
    step = ((s << 1) ^ ((s[top] ^ b) ? pp : 32'd0)) & m;
  endfunction

  function automatic logic [31:0] refl(input logic [31:0] v);
    for (int i = 0; i < 32; i++) refl[i] = v[31 - i];
  endfunction

  // its own copy of a byte being consumed, one bit per cycle like the RTL
  logic [31:0] s, n;
  logic [7:0]  hbyte;
  logic [3:0]  hcnt;
  always_comb begin
    n = clear ? (ini & mask) : s;
    if (bit_valid && !clear) n = step(n, bit_in, p, mask, wm1);     // clear wins over a bit
    if (hcnt != 4'd0) n = step(s, refin ? hbyte[0] : hbyte[7], p, mask, wm1);
  end
  always_ff @(posedge clk) begin
    s <= n;
    if (age == 2'd0) hcnt <= 4'd0;
    else if (byte_valid && hcnt == 4'd0) begin hbyte <= byte_in; hcnt <= 4'd8; end
    else if (hcnt != 4'd0) begin
      hbyte <= refin ? hbyte >> 1 : hbyte << 1;
      hcnt  <= hcnt - 4'd1;
    end
  end

  always_comb if (started) begin
`ifdef CAT
    if (nb == 4'd9 && !gap && !busy) assert (result == (R ? C : C << (6'd32 - W)));
`else
    assert (busy == (hcnt != 4'd0));
    assert (result == refl(s << sh));
`endif
  end
  wire _unused = &{check_ok, tx_bit, wm1_6[5], 1'b0};
endmodule
