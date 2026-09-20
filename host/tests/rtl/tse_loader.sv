`default_nettype none
// Byte-bus loader. Active while rst_n is low (its own reset is asserted in
// run mode). The RP2350 PIO presents one byte on ui_in per clock, changing on
// the falling edge, so din is sampled by one flop with half a period of
// margin (no synchronizer: the host drives the clock).
// Packet: 0xA5, CMD, then arguments. Bytes outside a packet are ignored.
//   0x01 WRITE  AH AL N  then 4*(N+1) bytes, words MSB first, address += 1
//   0x02 READ   AH AL N  then the loader streams 4*(N+1) bytes on dout
//   0x03 CHECK  CH CL    crc_ok = (CRC-16/CCITT-FALSE of all WRITE data == C)
//   0x04 CLEAR           clear config banks (and so the run key), CRC, crc_ok
//   0x05 SELFTEST        start the boot self-test
//   0x06 COMMIT          if crc_ok, write KEY to chip bank word 31 (lanes may run)
// Load-mode start: the first cycle after the loader leaves reset pulses
// c_clr, so the run key (loaded) and all config are cleared before any packet.
// Address: [15:12] 0 program word [8:0], 1 config bank [11:8] word [4:0],
// 2 status word [1:0]. Idle dout = status {key, crc_ok, st_done, st_pass,
// st_busy, 3'b101}; the fixed low bits let the host see the loader is alive.
module tse_loader #(
  parameter logic [15:0] KEY = 16'h7E5A
) (
  input  logic        clk,
  input  logic        rst_n,
  input  logic [7:0]  din,
  output logic [7:0]  dout,
  output logic        p_req,
  output logic        p_we,
  output logic [8:0]  p_addr,
  output logic [31:0] p_wdata,
  input  logic [31:0] p_rdata,
  output logic        c_we,
  output logic        c_clr,
  output logic [3:0]  c_bank,
  output logic [4:0]  c_word,
  output logic [31:0] c_wdata,
  input  logic [31:0] c_rdata,
  output logic        st_start,
  input  logic        st_busy,
  input  logic        st_done,
  input  logic        st_pass,
  input  logic [31:0] st_info,
  input  logic        key_ok
);
  typedef enum logic [2:0] {IDLE, CMD, ARG, WDATA, RDATA} state_t;
  state_t      state;
  logic [7:0]  b, cmd, n;
  logic [15:0] addr, crc, arg16;
  logic [1:0]  ac;                       // argument byte counter
  logic [9:0]  bi;                       // byte counter inside a data phase
  logic [31:0] sr, obuf, tmp;
  logic        crc_ok, started;
  logic [7:0]  status;

  function automatic logic [15:0] crc16_byte(input logic [15:0] c, input logic [7:0] d);
    logic [15:0] x;
    x = c ^ {d, 8'h00};
    for (int i = 0; i < 8; i++) x = x[15] ? {x[14:0], 1'b0} ^ 16'h1021 : {x[14:0], 1'b0};
    crc16_byte = x;
  endfunction

  assign status  = {key_ok, crc_ok, st_done, st_pass, st_busy, 3'b101};
  assign p_addr  = addr[8:0];
  assign c_bank  = addr[11:8];
  assign c_word  = addr[4:0];
  assign p_wdata = sr;
  assign c_wdata = sr;

  logic [9:0] total;                     // 4 * (N + 1) bytes
  logic [1:0] bc;
  logic [7:0] wi;
  assign total = {n, 2'b00} + 10'd4;
  assign bc    = bi[1:0];
  assign wi    = bi[9:2];

  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) begin
      state <= IDLE; b <= '0; cmd <= '0; n <= '0; addr <= '0; crc <= 16'hffff; arg16 <= '0;
      ac <= '0; bi <= '0; sr <= '0; obuf <= '0; tmp <= '0; crc_ok <= 1'b0; started <= 1'b0;
      dout <= '0; p_req <= 1'b0; p_we <= 1'b0; c_we <= 1'b0; c_clr <= 1'b0; st_start <= 1'b0;
    end else begin
      b <= din;
      p_req <= 1'b0; p_we <= 1'b0; c_we <= 1'b0; c_clr <= 1'b0; st_start <= 1'b0;
      if (state != RDATA) dout <= status;
      unique case (state)
        IDLE: if (b == 8'hA5) state <= CMD;
        CMD: begin
          cmd <= b; ac <= '0;
          unique case (b)
            8'h01, 8'h02, 8'h03: state <= ARG;
            8'h04: begin c_clr <= 1'b1; crc <= 16'hffff; crc_ok <= 1'b0; state <= IDLE; end
            8'h05: begin st_start <= 1'b1; state <= IDLE; end
            8'h06: begin                          // key lives in chip bank word 31
              if (crc_ok) begin c_we <= 1'b1; addr <= 16'h1F1F; sr <= {16'd0, KEY}; end
              state <= IDLE;
            end
            default: state <= IDLE;
          endcase
        end
        ARG: begin
          ac    <= ac + 2'd1;
          arg16 <= {arg16[7:0], b};
          if (cmd == 8'h03 && ac == 2'd1) begin
            crc_ok <= {arg16[7:0], b} == crc; state <= IDLE;
          end else if (ac == 2'd2) begin
            addr <= arg16; n <= b; bi <= '0;
            if (cmd == 8'h01) state <= WDATA;
            else              state <= RDATA;
          end
        end
        WDATA: begin
          sr  <= {sr[23:0], b};
          crc <= crc16_byte(crc, b);
          bi  <= bi + 10'd1;
          if (bc == 2'd3) begin
            p_req <= addr[15:12] == 4'd0; p_we <= addr[15:12] == 4'd0;
            c_we  <= addr[15:12] == 4'd1;
            if (bi + 10'd1 == total) state <= IDLE;
          end
        end
        RDATA: begin
          bi <= bi + 10'd1;
          if (bc == 2'd0 && 10'(wi) <= 10'(n)) begin
            p_req <= addr[15:12] == 4'd0;
            unique case (addr[15:12])
              4'd1:    tmp <= c_rdata;
              4'd2:    tmp <= (addr[1:0] == 2'd0) ? {24'd0, status}
                            : (addr[1:0] == 2'd1) ? st_info : {16'd0, crc};
              default: tmp <= '0;
            endcase
          end
          if (bc == 2'd2 && 10'(wi) <= 10'(n)) begin      // SRAM data valid two cycles after p_req is set
            obuf <= (addr[15:12] == 4'd0) ? p_rdata : tmp;
            addr <= addr + 16'd1;
          end else obuf <= {obuf[23:0], 8'h00};
          if (bi >= 10'd3) dout <= obuf[31:24];
          if (bi == total + 10'd2) state <= IDLE;
        end
        default: state <= IDLE;
      endcase
      if (state == WDATA && bc == 2'd0 && bi != 10'd0) addr <= addr + 16'd1;
      if (!started) begin started <= 1'b1; c_clr <= 1'b1; end
    end
endmodule
`default_nettype wire
