`default_nettype none
// Timing experiment top: an 8-word latch configuration bank in a 1x1 tile.
// uio_in[1:0] picks the command on each clock:
//   01 shift ui_in into the 32-bit write word (LSB byte first in, MSB last)
//   10 write the word to address ui_in[2:0]
//   11 read address ui_in[2:0], byte ui_in[4:3] onto uo_out; ui_in[7] clears the bank
// Two consumers give the latch outputs real downstream paths into flops:
// the registered readback byte, and a 16-bit counter compared with word 0
// (the shape of a timer compare in the lanes), registered on uio_out[0].
module tt_um_cfgbank_exp (
  input  wire [7:0] ui_in,
  output wire [7:0] uo_out,
  input  wire [7:0] uio_in,
  output wire [7:0] uio_out,
  output wire [7:0] uio_oe,
  input  wire       ena,
  input  wire       clk,
  input  wire       rst_n
);
  localparam int WORDS = 8;
  logic [31:0] wdata;
  logic [2:0]  rd_addr;
  logic [1:0]  rd_byte;
  logic        busy, clr;
  logic [31:0] rd_data;
  logic [WORDS*32-1:0] q;
  logic [15:0] cnt;
  logic [7:0]  out_q;
  logic        match_q;

  wire [1:0] cmd = uio_in[1:0];

  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) begin
      wdata <= '0; rd_addr <= '0; rd_byte <= '0; clr <= 1'b0;
      cnt <= '0; out_q <= '0; match_q <= 1'b0;
    end else begin
      clr <= cmd == 2'b11 && ui_in[7];
      if (cmd == 2'b01) wdata <= {ui_in, wdata[31:8]};
      if (cmd == 2'b11) begin rd_addr <= ui_in[2:0]; rd_byte <= ui_in[4:3]; end
      cnt     <= cnt + 16'd1;
      out_q   <= rd_data[8*rd_byte +: 8];
      match_q <= cnt == q[15:0];
    end

  tse_cfg_bank #(.WORDS(WORDS)) bank (
    .clk, .rst_n, .clr,
    .we(cmd == 2'b10), .addr(ui_in[2:0]), .wdata, .busy,
    .rd_addr, .rd_data, .q
  );

  assign uo_out  = out_q;
  assign uio_out = {6'd0, busy, match_q};
  assign uio_oe  = 8'h03;
  wire _unused = &{ena, uio_in[7:2], ui_in[6:5], q[WORDS*32-1:16], 1'b0};
endmodule
`default_nettype wire
