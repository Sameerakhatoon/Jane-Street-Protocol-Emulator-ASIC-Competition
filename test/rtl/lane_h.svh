// Lane test harness, included inside a testbench module: one tse_lane with
// its configuration bank, a 128-word program store and a 128-word data
// window behind the fixed slots of the chip (cycle count cnt restarts when
// run rises, so cycle 0 is the first running cycle; lane LANE fetches when
// cnt mod 4 == LANE and owns the data cycles cnt mod 16 == 4 x LANE + 3),
// chip flags and a timestamp. Instruction encoders build words from the
// generated ISA package. The testbench sets LANE with `define LANE_TB.
`ifndef LANE_TB
`define LANE_TB 0
`endif
localparam int LANE = `LANE_TB;

logic clk = 0, rst_n = 0, run = 0;
always #5 clk = ~clk;
longint cyc;                                  // cycles since run rose
always @(posedge clk) cyc <= run ? cyc + 1 : 0;

// ---------------- instruction encoders
function automatic logic [15:0] f(input int v, input int lo); return 16'(v << lo); endfunction
function automatic logic [15:0] SET(input int d, v);
    return tse_isa_pkg::SET_MATCH | f(d, tse_isa_pkg::SET_DST_LO) | f(v, tse_isa_pkg::SET_IMM_LO);
endfunction
function automatic logic [15:0] JMP(input int c, t);
    return tse_isa_pkg::JMP_MATCH | f(c, tse_isa_pkg::JMP_COND_LO) | f(t, tse_isa_pkg::JMP_TARGET_LO);
endfunction
function automatic logic [15:0] WAITI(input int pol, src, idx, to, alt, d);
    return tse_isa_pkg::WAIT_MATCH | f(pol, tse_isa_pkg::WAIT_POL_LO) | f(src, tse_isa_pkg::WAIT_SRC_LO)
         | f(idx, tse_isa_pkg::WAIT_IDX_LO) | f(to, tse_isa_pkg::WAIT_TO_LO) | f(alt, tse_isa_pkg::WAIT_ALT_LO)
         | f(d, tse_isa_pkg::WAIT_DELAY_LO);
endfunction
function automatic logic [15:0] MOV(input int d, s, dl);
    return tse_isa_pkg::MOV_MATCH | f(d, tse_isa_pkg::MOV_DST_LO) | f(s, tse_isa_pkg::MOV_SRC_LO)
         | f(dl, tse_isa_pkg::MOV_DELAY_LO);
endfunction
function automatic logic [15:0] OUT(input int d, n, dl);
    return tse_isa_pkg::OUT_MATCH | f(d, tse_isa_pkg::OUT_DST_LO) | f(n - 1, tse_isa_pkg::OUT_N_LO)
         | f(dl, tse_isa_pkg::OUT_DELAY_LO);
endfunction
function automatic logic [15:0] IN(input int s, n, dl);
    return tse_isa_pkg::IN_MATCH | f(s, tse_isa_pkg::IN_SRC_LO) | f(n - 1, tse_isa_pkg::IN_N_LO)
         | f(dl, tse_isa_pkg::IN_DELAY_LO);
endfunction
function automatic logic [15:0] CSRW(input int a); return tse_isa_pkg::CSRW_MATCH | f(a, tse_isa_pkg::CSRW_ADDR_LO); endfunction
function automatic logic [15:0] CSRR(input int a); return tse_isa_pkg::CSRR_MATCH | f(a, tse_isa_pkg::CSRR_ADDR_LO); endfunction
function automatic logic [15:0] DLY(input int d);  return tse_isa_pkg::DLY_MATCH | f(d, tse_isa_pkg::DLY_D_LO); endfunction
function automatic logic [15:0] SS(input logic [15:0] w, input int s); return w | f(s, tse_isa_pkg::SS_LO); endfunction

// ---------------- lane, bank, memories
logic [1023:0] cfg;
logic          l_cfg_we, cfg_clr = 0;
logic [4:0]    l_cfg_word;
logic [31:0]   l_cfg_wdata, bank_rd;
logic [4:0]    bank_ra = 0;
logic          h_we = 0, h_re = 0, h_tx_we = 0, h_rx_re = 0;
logic [7:0]    h_addr = 0;
logic [31:0]   h_wdata = 0, h_rdata, h_tx_data = 0, h_rx_data;
logic [1:0]    h_flags;
logic          ev_valid, sig_we;
logic [5:0]    ev_code;
logic [7:0]    ev_data;
logic [3:0]    irq, sig_mask, sig_val;
logic          halted;
logic          imem_req, imem_gnt, dmem_req, dmem_we, dmem_gnt;
logic [6:0]    dmem_addr;
logic [31:0]   dmem_wdata, dmem_rdata, dwin [128];
logic [6:0]    imem_addr;
logic [31:0]   rdata;
logic [3:0]    vin = 0, vout, voe;
logic [5:0]    chip_flags = 0;
logic [31:0]   ts = 0;
always @(posedge clk) ts <= ts + 1;
logic [31:0]   mem [128];
logic [3:0]    cnt;
int            fetches;

always_ff @(posedge clk) cnt <= run ? cnt + 4'd1 : 4'd0;
assign imem_gnt = run && imem_req && cnt[1:0] == 2'(LANE);
assign dmem_gnt = run && dmem_req && cnt == 4'(4 * LANE + 3);
always_ff @(posedge clk) begin
    if (imem_gnt) begin rdata <= mem[imem_addr]; fetches <= fetches + 1; end
    if (dmem_gnt && dmem_we) dwin[dmem_addr] <= dmem_wdata;
    if (dmem_gnt && !dmem_we) dmem_rdata <= dwin[dmem_addr];
end

// while stopped the testbench owns the bank, while running the lane does
logic        tb_we = 0;
logic [4:0]  tb_word = 0;
logic [31:0] tb_wdata = 0;
tse_cfg_bank #(.WORDS(32), .LATCH(1), .MASK(tse_regs_pkg::LANE_BANK_MASK)) u_bank (
    .clk, .rst_n, .clr(cfg_clr), .we(run ? l_cfg_we : tb_we), .addr(run ? l_cfg_word : tb_word),
    .wdata(run ? l_cfg_wdata : tb_wdata), .busy(), .rd_addr(bank_ra), .rd_data(bank_rd), .q(cfg)
);

tse_lane #(.K(2), .PW(16), .V(4)) dut (
    .clk, .rst_n, .run, .cfg, .cfg_we(l_cfg_we), .cfg_word(l_cfg_word), .cfg_wdata(l_cfg_wdata),
    .h_we, .h_re, .h_addr, .h_wdata, .h_rdata, .h_tx_we, .h_tx_data, .h_rx_re, .h_rx_data, .h_flags,
    .dmem_req, .dmem_we, .dmem_addr, .dmem_wdata, .dmem_gnt, .dmem_rdata,
    .ev_valid, .ev_code, .ev_data, .irq, .halted, .sig_we, .sig_mask, .sig_val, .imem_req, .imem_addr, .imem_gnt, .imem_rdata(rdata),
    .chip_flags, .ts, .vin_p(vin), .vin_n(vin), .vout, .voe
);

// the bank starts cleared, as after the loader BEGIN
initial begin wait (rst_n); @(negedge clk) cfg_clr = 1; @(negedge clk) cfg_clr = 0; end

// ---------------- helpers
int errors = 0;
task automatic check(input bit ok, input string what);
    if (!ok) begin errors++; $display("FAIL %s", what); end
    else $display("info %s", what);
endtask

// write a class-L register through the bank while the lane is stopped
task automatic cfg_set(input logic [7:0] a, input logic [31:0] v);
    @(negedge clk); tb_we = 1; tb_word = tse_regs_pkg::lane_reg_word(a); tb_wdata = v;
    @(negedge clk); tb_we = 0;
    repeat (3) @(negedge clk);
endtask

function automatic logic [31:0] bank_word(input logic [7:0] a);
    return cfg[32*tse_regs_pkg::lane_reg_word(a) +: 32];
endfunction

task automatic load(input logic [15:0] p []);
    foreach (mem[i]) mem[i] = {JMP(0, 0), JMP(0, 0)};
    for (int i = 0; i < p.size(); i += 2) mem[i / 2] = {(i + 1 < p.size()) ? p[i + 1] : JMP(0, i + 1), p[i]};
endtask

task automatic start();
    @(negedge clk) run = 0; repeat (3) @(negedge clk); fetches = 0;
    @(negedge clk) run = 1;
endtask

task automatic stop();
    @(negedge clk) run = 0; @(negedge clk);
endtask

// dispatch cycles of the instructions at pc (the first 16 of each), recorded while running
longint disp [256][16];
int     ndisp [256];
always @(posedge clk)
    if (run && dut.u_seq.avail && !dut.u_seq.held) begin
        if (ndisp[dut.u_seq.pc] < 16) disp[dut.u_seq.pc][ndisp[dut.u_seq.pc]] = cyc;
        ndisp[dut.u_seq.pc]++;
    end
task automatic clear_disp(); foreach (ndisp[i]) ndisp[i] = 0; endtask
function automatic longint at(input int pc, input int k = 0);
    return ndisp[pc] > k ? disp[pc][k] : -1;
endfunction

// program under construction: emit() appends, load_q() loads it
logic [15:0] pq [$];
function automatic void emit(input logic [15:0] w); pq.push_back(w); endfunction
task automatic load_q();
    logic [15:0] p [];
    p = new[pq.size()];
    foreach (pq[i]) p[i] = pq[i];
    load(p);
endtask

task automatic finish(input string name);
    if (errors == 0) $display("PASS %s", name);
    else             $display("FAIL %s errors=%0d", name, errors);
    $finish;
endtask
