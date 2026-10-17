// Formal harness for every output of tse_crc, immediate assertions only.
// All configuration words are free (any width, any left-aligned values,
// both reflections) and every input is free in every cycle: clear, single
// bits, bytes, tx_load and tx_shift. A shadow register written from the
// unit's definition steps beside it:
//   clear: init; tx_load: register XOR xorout; tx_shift: shift left;
//   otherwise a data bit (or the next bit of a byte) enters MSB first,
// with that priority, and a byte is consumed one bit per cycle for 8 cycles.
// The outputs must be the textbook functions of the shadow: busy, tx_bit
// (bit 31), check_ok (register XOR xorout equals the check value) and result
// (right-aligned, or reflected over 32 bits with refout).
module crc_full (
  input logic       clk,
  input logic       clear,
  input logic       bit_valid,
  input logic       bit_in,
  input logic       byte_valid,
  input logic [7:0] byte_in,
  input logic       tx_load,
  input logic       tx_shift
);
  (* anyconst *) logic [31:0] poly, ini, xo, check;
  (* anyconst *) logic [4:0]  wm1;
  (* anyconst *) logic        refin, refout;

  logic init = 1'b1;
  always_ff @(posedge clk) init <= 1'b0;

  logic        busy, tx_bit, check_ok;
  logic [31:0] result;
  tse_crc dut (
    .clk, .rst_n(!init), .cfg_poly(poly), .cfg_init(ini), .cfg_xo(xo), .cfg_check(check),
    .cfg_wm1(wm1), .cfg_refin(refin), .cfg_refout(refout),
    .clear, .bit_valid, .bit_in, .byte_valid, .byte_in, .busy,
    .tx_load, .tx_shift, .tx_bit, .result, .check_ok
  );

  function automatic logic [31:0] refl(input logic [31:0] v);
    for (int i = 0; i < 32; i++) refl[i] = v[31 - i];
  endfunction

  logic [31:0] r;
  logic [7:0]  hb;
  logic [3:0]  hn;
  wire         hbusy = hn != 4'd0;
  wire         d = hbusy ? (refin ? hb[0] : hb[7]) : bit_in;
  always_ff @(posedge clk)
    if (init) begin
      r <= 32'd0; hn <= 4'd0;
    end else begin
      if (clear)                   r <= ini;
      else if (tx_load)            r <= r ^ xo;
      else if (tx_shift)           r <= r << 1;
      else if (bit_valid || hbusy) r <= (r << 1) ^ ((r[31] ^ d) ? poly : 32'd0);
      if (byte_valid && !hbusy) begin
        hb <= byte_in; hn <= 4'd8;
      end else if (hbusy) begin
        hb <= refin ? hb >> 1 : hb << 1;
        hn <= hn - 4'd1;
      end
    end

  wire [31:0] t = r ^ xo;
  always_comb if (!init) begin
    assert (busy == hbusy);
    assert (tx_bit == r[31]);
    assert (check_ok == (t == check));
    assert (result == (refout ? refl(t) : t >> (5'd31 - wm1)));
  end
endmodule
