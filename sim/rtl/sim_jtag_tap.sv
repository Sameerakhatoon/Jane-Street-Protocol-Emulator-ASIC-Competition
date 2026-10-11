`default_nettype none
// Stand-in IEEE 1149.1 TAP for the remote_bitbang bridge. Not chip RTL.
// TCK, TMS and TDI are pins sampled with the system clock (two flops), the
// way a lane or a pin-sampled target sees them. TMS and TDI are taken on a
// rising TCK edge, TDO changes on a falling one. IR is 4 bits, captures
// 0001; IDCODE (0x1) is selected after Test-Logic-Reset, every other
// instruction is BYPASS.
module sim_jtag_tap #(
  parameter logic [31:0] IDCODE = 32'h15E0_0001
) (
  input  wire  clk,
  input  wire  rst_n,
  input  wire  tck,
  input  wire  tms,
  input  wire  tdi,
  output logic tdo
);
  typedef enum logic [3:0] {
    TLR, RTI, SEL_DR, CAP_DR, SH_DR, EX1_DR, PA_DR, EX2_DR, UPD_DR,
    SEL_IR, CAP_IR, SH_IR, EX1_IR, PA_IR, EX2_IR, UPD_IR
  } state_t;

  logic [2:0]  s_tck;
  logic [1:0]  s_tms, s_tdi;
  logic        rise, fall;
  state_t      st, nx;
  logic [3:0]  ir, ir_sr;
  logic [31:0] dr;

  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) begin
      s_tck <= '0; s_tms <= '1; s_tdi <= '0;
    end else begin
      s_tck <= {s_tck[1:0], tck}; s_tms <= {s_tms[0], tms}; s_tdi <= {s_tdi[0], tdi};
    end
  assign rise = s_tck[1] && !s_tck[2];
  assign fall = !s_tck[1] && s_tck[2];

  always_comb
    unique case (st)
      TLR:    nx = s_tms[1] ? TLR    : RTI;
      RTI:    nx = s_tms[1] ? SEL_DR : RTI;
      SEL_DR: nx = s_tms[1] ? SEL_IR : CAP_DR;
      CAP_DR: nx = s_tms[1] ? EX1_DR : SH_DR;
      SH_DR:  nx = s_tms[1] ? EX1_DR : SH_DR;
      EX1_DR: nx = s_tms[1] ? UPD_DR : PA_DR;
      PA_DR:  nx = s_tms[1] ? EX2_DR : PA_DR;
      EX2_DR: nx = s_tms[1] ? UPD_DR : SH_DR;
      UPD_DR: nx = s_tms[1] ? SEL_DR : RTI;
      SEL_IR: nx = s_tms[1] ? TLR    : CAP_IR;
      CAP_IR: nx = s_tms[1] ? EX1_IR : SH_IR;
      SH_IR:  nx = s_tms[1] ? EX1_IR : SH_IR;
      EX1_IR: nx = s_tms[1] ? UPD_IR : PA_IR;
      PA_IR:  nx = s_tms[1] ? EX2_IR : PA_IR;
      EX2_IR: nx = s_tms[1] ? UPD_IR : SH_IR;
      UPD_IR: nx = s_tms[1] ? SEL_DR : RTI;
      default: nx = TLR;
    endcase

  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) begin
      st <= TLR; ir <= 4'h1; ir_sr <= '0; dr <= '0; tdo <= 1'b0;
    end else begin
      if (rise) begin
        st <= nx;
        unique case (st)
          TLR:     ir <= 4'h1;
          CAP_DR:  dr <= (ir == 4'h1) ? IDCODE : 32'd0;
          SH_DR:   dr <= (ir == 4'h1) ? {s_tdi[1], dr[31:1]} : {31'd0, s_tdi[1]};
          CAP_IR:  ir_sr <= 4'b0001;
          SH_IR:   ir_sr <= {s_tdi[1], ir_sr[3:1]};
          UPD_IR:  ir <= ir_sr;
          default: ;
        endcase
      end
      if (fall)
        tdo <= (st == SH_DR) ? dr[0] : (st == SH_IR) ? ir_sr[0] : 1'b0;
    end
endmodule
`default_nettype wire
