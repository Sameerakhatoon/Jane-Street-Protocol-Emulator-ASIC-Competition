`default_nettype none
// Harness stand-in with the Tiny Tapeout pin list, for tests of the harness
// and its bridges without the lanes. Not chip RTL.
//   ui_in[0] -> uo_out[0]  UART 8N1: receive a byte, upper-case it, send it back
//   ui_in[1] -> uo_out[1]  a 2-flop wire, so Ethernet bits cross the model
//   uo_out[2]              UART receiver busy
//   ui_in[2..4] -> uo_out[3] JTAG TCK, TMS, TDI -> TDO (sim_jtag_tap)
//   ui_in[5], uio[2]         SWCLK, SWDIO (sim_swd_dp)
module tt_um_uart_echo #(
  parameter int DIV = 521                       // 60 MHz / 115200 baud, rounded
) (
  input  wire [7:0] ui_in,
  output wire [7:0] uo_out,
  input  wire [7:0] uio_in,
  output wire [7:0] uio_out,
  output wire [7:0] uio_oe,
  input  wire       ena,
  input  wire       clk,
  input  wire       rst_n
);
  logic [1:0]  rx_sync;
  logic [15:0] rcnt;
  logic [3:0]  rbit;
  logic [7:0]  rshift, hold;
  logic        rbusy, have;

  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) begin
      rx_sync <= 2'b11; rbusy <= 1'b0; have <= 1'b0;
      rcnt <= '0; rbit <= '0; rshift <= '0; hold <= '0;
    end else begin
      rx_sync <= {rx_sync[0], ui_in[0]};
      have <= 1'b0;
      if (!rbusy) begin
        if (!rx_sync[1]) begin rbusy <= 1'b1; rcnt <= 16'(DIV / 2); rbit <= '0; end
      end else if (rcnt != 0) begin
        rcnt <= rcnt - 1'b1;
      end else begin
        rcnt <= 16'(DIV - 1);
        rbit <= rbit + 1'b1;
        if (rbit >= 4'd1 && rbit <= 4'd8) rshift <= {rx_sync[1], rshift[7:1]};
        if (rbit == 4'd9) begin
          rbusy <= 1'b0;
          have  <= 1'b1;
          hold  <= (rshift >= "a" && rshift <= "z") ? rshift - 8'd32 : rshift;
        end
      end
    end

  logic [15:0] tcnt;
  logic [3:0]  tbit;
  logic [9:0]  tshift;
  logic        tbusy, pend, txd;               // pend: one-byte holding register

  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) begin
      txd <= 1'b1; tbusy <= 1'b0; pend <= 1'b0; tcnt <= '0; tbit <= '0; tshift <= '1;
    end else begin
      if (have) pend <= 1'b1;
      if (!tbusy) begin
        if (pend) begin
          tbusy <= 1'b1; pend <= have; tshift <= {1'b1, hold, 1'b0}; tcnt <= '0; tbit <= '0;
        end
      end else if (tcnt != 0) begin
        tcnt <= tcnt - 1'b1;
      end else begin
        txd    <= tshift[0];
        tshift <= {1'b1, tshift[9:1]};
        tcnt   <= 16'(DIV - 1);
        tbit   <= tbit + 1'b1;
        if (tbit == 4'd10) begin tbusy <= 1'b0; txd <= 1'b1; end
      end
    end

  logic [1:0] wire_q;
  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) wire_q <= '0;
    else        wire_q <= {wire_q[0], ui_in[1]};

  logic tdo, swdio_out, swdio_oe;
  sim_jtag_tap u_tap (.clk, .rst_n, .tck(ui_in[2]), .tms(ui_in[3]), .tdi(ui_in[4]), .tdo);
  sim_swd_dp u_dp (.clk, .rst_n, .swclk(ui_in[5]), .swdio_in(uio_in[2]), .swdio_out, .swdio_oe);

  assign uo_out  = {4'b0, tdo, rbusy, wire_q[1], txd};
  assign uio_out = {5'b0, swdio_out, 2'b0};
  assign uio_oe  = {5'b0, swdio_oe, 2'b0};
  wire _unused = &{ena, ui_in[7:6], uio_in[7:3], uio_in[1:0], 1'b0};
endmodule
`default_nettype wire
