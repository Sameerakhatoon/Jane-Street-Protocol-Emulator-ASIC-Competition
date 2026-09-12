`default_nettype none
// Lint harness for the generated packages: Verilator checks a package only
// through a module that uses it.
module tse_pkg_lint (
  input  logic [15:0] w,
  input  logic [7:0]  a,
  output logic [4:0]  iclass,
  output logic        legal,
  output logic [2:0]  rclass,
  output logic [4:0]  word
);
  assign iclass = tse_isa_pkg::tse_decode(w);
  assign legal  = tse_isa_pkg::tse_legal(w);
  assign rclass = tse_regs_pkg::lane_reg_class(a);
  assign word   = tse_regs_pkg::lane_reg_word(a);
endmodule
`default_nettype wire
