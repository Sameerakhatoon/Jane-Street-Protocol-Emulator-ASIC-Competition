`default_nettype none
// Runs tse_seq_spec for lanes 0 to 3 at fetch latency 1 and 2 (eight copies)
// on the same program and inputs, and writes one trace line per copy and
// cycle. Test n reads <dir>/t<n>.prog (256 words), t<n>.cfg (start pc,
// LANE_CFG, GUARD01, GUARD23) and t<n>.env (one 64-bit input word per cycle:
// [3:0] pins, [11:4] ev, [19:12] flag, [27:20] chip, [28] hrx_full,
// [29] htx_full, [63:32] dval) and writes t<n>.trace.
// plusargs: +dir=<path> +tests=<n> +cycles=<n>
module tb_seq_spec;
    localparam int N = 8;
    localparam int MAXCYC = 1 << 16;

    logic        clk = 1'b0;
    logic        rst = 1'b1;
    logic [15:0] prog [256];
    logic [31:0] cfg [4];
    logic [63:0] env [MAXCYC];
    /* verilator lint_off UNUSEDSIGNAL */
    logic [63:0] e;                     // bits 31:30 unused
    /* verilator lint_on UNUSEDSIGNAL */

    logic [7:0]  pcs [N];
    logic        o_issue [N], o_fetch [N], o_halted [N], o_tflag [N], o_aflag [N], o_cfg_we [N];
    logic [6:0]  o_fword [N];
    logic [15:0] o_x [N], o_y [N], o_t [N], o_c [N];
    logic [7:0]  o_cfg_addr [N];
    logic [31:0] o_cfg_wdata [N];

    for (genvar i = 0; i < N; i++) begin : g_spec
        tse_seq_spec #(.LANE(i / 2), .FETCH_LATENCY(i % 2 + 1)) u_spec (
            .clk, .rst, .start_pc(cfg[0][7:0]), .cfg_lane(cfg[1]), .cfg_guard01(cfg[2]),
            .cfg_guard23(cfg[3]), .pc(pcs[i]), .instr(prog[pcs[i]]), .pins(e[3:0]), .ev(e[11:4]),
            .flag(e[19:12]), .chip(e[27:20]), .hrx_full(e[28]), .htx_full(e[29]), .dval(e[63:32]),
            .issue(o_issue[i]), .fetch(o_fetch[i]), .fetch_word(o_fword[i]), .halted(o_halted[i]),
            .x(o_x[i]), .y(o_y[i]), .t(o_t[i]), .c(o_c[i]), .tflag(o_tflag[i]), .aflag(o_aflag[i]),
            .cfg_we(o_cfg_we[i]), .cfg_addr(o_cfg_addr[i]), .cfg_wdata(o_cfg_wdata[i])
        );
    end

    string dir;
    int    tests, cycles, fd;

    initial begin
        if (!$value$plusargs("dir=%s", dir)) dir = ".";
        if (!$value$plusargs("tests=%d", tests)) tests = 1;
        if (!$value$plusargs("cycles=%d", cycles)) cycles = 1000;
        e = 64'd0;
        for (int n = 0; n < tests; n++) begin
            $readmemh($sformatf("%s/t%0d.prog", dir, n), prog);
            $readmemh($sformatf("%s/t%0d.cfg", dir, n), cfg);
            $readmemh($sformatf("%s/t%0d.env", dir, n), env, 0, cycles - 1);
            fd = $fopen($sformatf("%s/t%0d.trace", dir, n), "w");
            rst = 1'b1;
            #1 clk = 1'b1;
            #1 clk = 1'b0;
            rst = 1'b0;
            for (int k = 0; k < cycles; k++) begin
                e = env[k];
                #1;
                for (int i = 0; i < N; i++)
                    $fwrite(fd, "%0d %0d %0d %h %0d %h %0d %h %h %h %h %0d %0d %0d %h %h\n", k, i, o_issue[i],
                            pcs[i], o_fetch[i], o_fword[i], o_halted[i], o_x[i], o_y[i], o_t[i], o_c[i],
                            o_tflag[i], o_aflag[i], o_cfg_we[i], o_cfg_addr[i], o_cfg_wdata[i]);
                clk = 1'b1;
                #1 clk = 1'b0;
            end
            $fclose(fd);
        end
        $finish;
    end
endmodule
`default_nettype wire
