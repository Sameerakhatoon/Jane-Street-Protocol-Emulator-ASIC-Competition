`default_nettype none
// Stand-in SWD debug port for the remote_bitbang bridge. Not chip RTL and
// not a full SW-DP: DP registers only, no AP, no WAIT or FAULT.
//   read  0x0 DPIDR (constant)     write 0x0 ABORT (ignored)
//   read  0x4 CTRL/STAT: the written value with every power-up request
//         bit (30, 28) copied into its acknowledge bit (31, 29)
//   write 0x4 CTRL/STAT            write 0x8 SELECT
//   read  0xC RDBUFF: SELECT, so a test can see a write land
// SWCLK is sampled with the system clock; SWDIO is taken on a rising edge
// and driven right after one, so the host reads it while SWCLK is low.
// 50 ones in a row are a line reset; a request with a bad start, stop,
// park or parity bit, or one for an AP, is ignored until the next reset.
module sim_swd_dp #(
  parameter logic [31:0] DPIDR = 32'h0BC1_1477
) (
  input  wire  clk,
  input  wire  rst_n,
  input  wire  swclk,
  input  wire  swdio_in,
  output logic swdio_out,
  output logic swdio_oe
);
  typedef enum logic [3:0] {LOCK, RESET, IDLE, REQ, ACK, RDATA, RTRN, WTRN, WDATA} state_t;

  logic [2:0]  s_clk;
  logic [1:0]  s_dio;
  logic        rise, bit_in;
  state_t      st;
  logic [5:0]  ones, n;
  logic [7:0]  req;
  logic [32:0] sr;
  logic [31:0] ctrl, sel;
  logic        good, rnw;
  logic [3:0]  addr;

  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) begin
      s_clk <= '0; s_dio <= '1;
    end else begin
      s_clk <= {s_clk[1:0], swclk}; s_dio <= {s_dio[0], swdio_in};
    end
  assign rise   = s_clk[1] && !s_clk[2];
  assign bit_in = s_dio[1];

  // req as shifted in, LSB first: {park, stop, parity, A3, A2, RnW, APnDP, start}
  wire [7:0] r = {bit_in, req[7:1]};
  assign good = r[0] && !r[6] && r[7] && !r[1] && (r[5] == ^r[4:1]);
  assign rnw  = req[2];
  assign addr = {req[4:3], 2'b00};
  wire   _unused = req[0];          // the start bit, always 1

  function automatic logic [31:0] rd_reg(input logic [3:0] a);
    unique case (a)
      4'h0:    rd_reg = DPIDR;
      4'h4:    rd_reg = ctrl | {ctrl[30], 1'b0, ctrl[28], 29'd0};
      4'hC:    rd_reg = sel;
      default: rd_reg = 32'd0;
    endcase
  endfunction

  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) begin
      st <= LOCK; ones <= '0; n <= '0; req <= '0; sr <= '0; ctrl <= '0; sel <= '0;
      swdio_out <= 1'b1; swdio_oe <= 1'b0;
    end else if (rise) begin
      ones <= !bit_in ? 6'd0 : (ones == 6'd63) ? ones : ones + 6'd1;
      n    <= n + 6'd1;
      if (bit_in && ones >= 6'd49 && !swdio_oe) begin
        st <= RESET;
      end else begin
        unique case (st)
          LOCK:  ;
          RESET: if (!bit_in) st <= IDLE;
          IDLE:  if (bit_in) begin st <= REQ; req <= 8'h80; n <= 6'd1; end
          REQ: begin
            req <= r;
            if (n == 6'd7) begin
              if (good) begin
                st <= ACK; n <= '0;
              end else st <= LOCK;
            end
          end
          ACK: begin                                       // trn, then OK = 1, 0, 0
            swdio_oe  <= 1'b1;
            swdio_out <= n == 6'd0;
            if (n == 6'd2) begin
              n <= '0;
              if (rnw) begin
                st <= RDATA;
                sr <= {^rd_reg(addr), rd_reg(addr)};
              end else st <= WTRN;
            end
          end
          RDATA: begin                                     // 32 data bits, parity, release
            swdio_out <= sr[0];
            sr <= {1'b0, sr[32:1]};
            if (n == 6'd33) begin
              swdio_oe <= 1'b0; swdio_out <= 1'b1; st <= RTRN;
            end
          end
          RTRN: st <= IDLE;                                // the host's turnaround clock
          WTRN: begin                                      // release, one turnaround clock
            swdio_oe <= 1'b0; swdio_out <= 1'b1;
            if (n == 6'd1) begin st <= WDATA; n <= '0; end
          end
          WDATA: begin
            sr <= {bit_in, sr[32:1]};
            if (n == 6'd32) begin
              st <= IDLE;
              if (^{bit_in, sr[32:1]} == 1'b0) begin       // even parity over data and parity bit
                if (addr == 4'h4) ctrl <= sr[32:1];
                if (addr == 4'h8) sel  <= sr[32:1];
              end
            end
          end
          default: st <= LOCK;
        endcase
      end
    end
endmodule
`default_nettype wire
