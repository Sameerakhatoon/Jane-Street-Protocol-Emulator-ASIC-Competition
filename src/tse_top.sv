`default_nettype none
// TSE chip core: LANES lanes (3 in 6x4, 4 in 8x4), one SRAM, configuration
// banks, crossbar, event unit, self-test, and one host bus driven by the
// command decoder from the load-mode byte bus or the run-mode SPI target.
// Host map (16-bit word addresses, every name from tse_regs_pkg):
//   0x0000 program SRAM, window w at 0x80 x w; 0x0400 data window (SRAM
//   window 3 with 3 lanes, the latch data store with 4); 0x0800 fallback
//   program store; 0x1000 + 0x100 x lane + reg lane registers; 0x2000
//   crossbar; 0x3000 event unit; 0x4000 chip registers and mailboxes.
//   Unmapped addresses read 0 and ignore writes.
// Refused, setting the sticky host error: a class L or P lane write while
// that lane runs or is about to; an SRAM write into a program window a
// running lane fetches from; a fallback store write while a running lane
// fetches from it; any SRAM access during the self-test, or SRAM program
// access while lanes run with 4 lanes (no system slot); RUN in run mode
// while not loaded (in load mode the runtime flops are in reset and a RUN
// write does nothing). A refused read returns 0.
// Chip configuration (class L) is one 64-word bank addressed {bank, word}
// with the bank and word numbers of chip_reg_cfg() in the register package:
// bank 0 crossbar (PAD_OUT, LANE_IN), bank 1 event (PIN_OPT, EV_CTL and the
// trigger words). LANE_IN words of lanes not built store nothing. Package
// names are written out in full (pkg::NAME) because Yosys takes neither a
// wildcard nor a header import.
// RUN takes effect at the end of the 16-cycle slot frame and only while
// loaded, so every lane's data cycles have a fixed phase from its start.
module tse_top #(
    parameter int LANES      = 3,
    parameter bit CFG_LATCH  = 1,
    parameter int EVQ_DEPTH  = 4,
    parameter int DS_WORDS   = 16
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [15:0] pad_in,          // {uio_in, ui_in}
    output logic [15:0] pad_out,         // {uio_out, uo_out}
    output logic [15:0] pad_oe,
    output logic        load_mode,
    output logic [7:0]  ld_dout,
    output logic        spi_en,
    output logic        spi_miso
);
    localparam int V  = 4;
    localparam int NI = LANES * V;

    // The lanes still decode their configuration in their own word layout, so
    // the lane bank stores that layout's bits plus the program register.
    localparam logic [1023:0] LANE_OWN = {
        {8{32'h0}},
        32'h0000_1FFF, 32'h01FF_FFFF, 32'h0000_0000, 32'hFFFF_FFFF,    // rule 1
        32'h0000_1FFF, 32'h01FF_FFFF, 32'h0000_0000, 32'hFFFF_FFFF,    // rule 0
        {2{32'h0}}, 32'h0FC0_0000, 32'h0000_0FFF,                     // WAIT alternate, RULE_CLK
        32'h0007_FFFF, 32'h00FF_FFFF, 32'h03FF_FFFF,                   // timer, lane
        32'h000F_FFFF, 32'h000F_FFFF, 32'h000F_FFFF,                   // DPLL
        32'h0000_0F3F, 32'h0000_007F, {4{32'hFFFF_FFFF}}              // line, CRC
    };
    localparam logic [1023:0] LANE_MASK = LANE_OWN | (1024'(tse_regs_pkg::LR_LANE_PROG_MASK) << (32 * tse_regs_pkg::LW_LANE_PROG));

    // chip bank word (bank x 32 + word) of each configuration register
    localparam int CW_XBAR       = 32 * int'(tse_regs_pkg::CBANK_XBAR);
    localparam int CW_EVENT      = 32 * int'(tse_regs_pkg::CBANK_EVENT);
    localparam int CW_PAD_OUT    = CW_XBAR + int'(tse_regs_pkg::CW_PAD_OUT);
    localparam int CW_LANE_IN    = CW_XBAR + int'(tse_regs_pkg::CW_LANE_IN);
    localparam int CW_PIN_OPT    = CW_EVENT + int'(tse_regs_pkg::CW_PIN_OPT);
    localparam int CW_EV_CTL     = CW_EVENT + int'(tse_regs_pkg::CW_EV_CTL);
    localparam int CW_TRIG_MATCH = CW_EVENT + int'(tse_regs_pkg::CW_TRIG_MATCH);
    localparam int CW_TRIG_WIN   = CW_EVENT + int'(tse_regs_pkg::CW_TRIG_WIN);
    localparam int CW_TRIG_CTL   = CW_EVENT + int'(tse_regs_pkg::CW_TRIG_CTL);

    function automatic logic [64*32-1:0] chip_mask();
        logic [64*32-1:0] m;
        m = '0;
        m[32*CW_XBAR +: 1024]  = tse_regs_pkg::CHIP_XBAR_MASK;
        m[32*CW_EVENT +: 1024] = tse_regs_pkg::CHIP_EVENT_MASK;
        for (int i = NI; i < tse_regs_pkg::HR_LANE_IN_COUNT; i++) m[32*(CW_LANE_IN + i) +: 32] = '0;
        chip_mask = m;
    endfunction
    localparam logic [64*32-1:0] CHIP_MASK = chip_mask();

    // ---------------- resets and mode
    logic eng_rst_n, ld_rst_n, sys_rst_n;
    tse_rst_sync u_rst (.clk, .rst_n, .eng_rst_n, .ld_rst_n, .sys_rst_n, .load_mode);

    // ---------------- host bus
    logic        h_we, h_re, h_err, h_rvalid;
    logic [15:0] h_addr;
    logic [31:0] h_wdata, h_rdata, rmux;
    logic        loaded, host_err, cfg_clr, bist_start;
    logic [31:0] load_crc;
    logic        s_valid, s_first;
    logic [7:0]  s_data;

    logic [3:0]       run_q;
    logic [LANES-1:0] lane_live, lane_run;
    logic             st_busy, st_done, st_pass;
    logic [13:0]      st_result;

    assign lane_run = run_q[LANES-1:0] | lane_live;

    tse_loader u_loader (
        .clk, .rst_n(sys_rst_n), .load_mode,
        .ld_data(pad_in[7:0]), .ld_strobe(pad_in[8]), .ld_frame(pad_in[9]), .ld_dout,
        .s_valid, .s_first, .s_data,
        .h_we, .h_re, .h_addr, .h_wdata, .h_rdata, .h_rvalid, .h_err,
        .lanes_idle(lane_run == '0), .bist_done(st_done), .bist_pass(st_pass), .halted(4'd0),
        .loaded, .host_err, .load_crc, .cfg_clr, .bist_start
    );

    // address decode
    logic [7:0]  hreg;
    logic [1:0]  hl;
    tse_regs_pkg::rclass_t     hcls;
    logic [4:0]  hword;
    tse_regs_pkg::chip_cfg_t ccfg;
    logic [5:0]  cw;
    logic        a_sram, a_dwin, a_fb, a_lane;
    logic [1:0]  a_win;
    logic [3:0]  busy_win;
    assign hreg   = h_addr[7:0];
    assign hl     = h_addr[9:8];
    assign hcls   = tse_regs_pkg::lane_reg_class(hreg);
    assign hword  = tse_regs_pkg::lane_reg_word(hreg);
    assign ccfg   = tse_regs_pkg::chip_reg_cfg(h_addr);
    assign cw     = {ccfg.bank, ccfg.word};
    assign a_sram = h_addr < tse_regs_pkg::REGION_SRAM_BASE + tse_regs_pkg::REGION_SRAM_SIZE;
    assign a_dwin = h_addr >= tse_regs_pkg::REGION_DATA_BASE && h_addr < tse_regs_pkg::REGION_DATA_BASE + tse_regs_pkg::REGION_DATA_SIZE;
    assign a_fb   = h_addr >= tse_regs_pkg::REGION_FALLBACK_BASE && h_addr < tse_regs_pkg::REGION_FALLBACK_BASE + tse_regs_pkg::REGION_FALLBACK_SIZE;
    assign a_lane = h_addr >= tse_regs_pkg::LANE_HOST_BASE && 32'(h_addr) < 32'(tse_regs_pkg::LANE_HOST_BASE) + LANES * 32'(tse_regs_pkg::LANE_HOST_STRIDE);
    assign a_win  = a_dwin ? 2'd3 : h_addr[8:7];

    logic [LANES*2-1:0] l_win;
    logic [LANES-1:0]   l_fb;
    logic               busy_fb;
    logic [LANES*1024-1:0] lcfg;
    always_comb begin
        busy_win = '0;
        busy_fb  = 1'b0;
        for (int i = 0; i < LANES; i++) begin
            l_win[2*i +: 2] = lcfg[1024*i + 32*tse_regs_pkg::LW_LANE_PROG + tse_regs_pkg::LR_LANE_PROG_WINDOW_LO +: 2];
            l_fb[i]         = lcfg[1024*i + 32*tse_regs_pkg::LW_LANE_PROG + tse_regs_pkg::LR_LANE_PROG_FALLBACK_LO];
            if (lane_run[i] && !l_fb[i]) busy_win[l_win[2*i +: 2]] = 1'b1;
            if (lane_run[i] && l_fb[i])  busy_fb = 1'b1;
        end
    end

    // with 4 lanes the data window is the latch store, not the SRAM
    logic a_mem, a_sram_only;
    assign a_mem       = a_sram || a_dwin || a_fb;
    assign a_sram_only = a_sram || (a_dwin && LANES < 4);

    logic acc, err_sram, err_fb, err_lane, err_run, sram_go;
    assign acc      = h_we || h_re;
    assign err_sram = a_sram_only && (st_busy || (LANES == 4 && lane_run != '0) || (h_we && busy_win[a_win]));
    assign err_fb   = a_fb && h_we && busy_fb;
    assign err_lane = a_lane && h_we && (hcls == tse_regs_pkg::RC_L || hcls == tse_regs_pkg::RC_P) && lane_run[hl];
    assign err_run  = h_we && h_addr == tse_regs_pkg::HR_RUN && !loaded && !load_mode;
    assign h_err    = acc && (err_sram || err_fb || err_lane || err_run);
    assign sram_go  = acc && a_mem && !err_sram && !err_fb;

    logic h_wr;                                   // an accepted register write
    assign h_wr = h_we && !h_err;

    // ---------------- program memory, data window, fallback store, self-test
    logic        pm_pend, pm_we, pm_dw, pm_fb, pm_gnt, pm_rd_q;
    logic [8:0]  pm_addr;
    logic [31:0] pm_wdata, pm_rdata;
    logic        st_m_en, st_m_req, st_m_we, frame_end;
    logic [8:0]  st_m_addr;
    logic [31:0] st_m_wdata;
    logic [3:0]  slot_cnt;
    logic [LANES-1:0]   l_req, l_gnt;
    logic [LANES*7-1:0] l_addr;
    logic [31:0]        l_rdata;

    // Lane data port (LDW, STW), one set per lane, protocol in tse_prog_mem.
    // The tse_lane ports dmem_req, dmem_we, dmem_addr, dmem_wdata, dmem_gnt
    // and dmem_rdata connect here; without them the port idles.
    logic [LANES-1:0]    l_dreq, l_dwe, l_dgnt;
    logic [LANES*7-1:0]  l_daddr;
    logic [LANES*32-1:0] l_dwdata;
    logic [31:0]         l_drdata;
    assign l_dreq   = '0;
    assign l_dwe    = '0;
    assign l_daddr  = '0;
    assign l_dwdata = '0;

    always_ff @(posedge clk or negedge sys_rst_n)
        if (!sys_rst_n) begin
            pm_pend <= 1'b0; pm_we <= 1'b0; pm_dw <= 1'b0; pm_fb <= 1'b0; pm_addr <= '0;
            pm_wdata <= '0; pm_rd_q <= 1'b0;
        end else begin
            pm_rd_q <= pm_gnt && !pm_we;
            if (pm_gnt) pm_pend <= 1'b0;
            if (sram_go) begin
                pm_pend <= 1'b1; pm_we <= h_we; pm_wdata <= h_wdata;
                pm_dw <= a_dwin; pm_fb <= a_fb; pm_addr <= h_addr[8:0];
            end
        end

    tse_prog_mem #(.LANES(LANES), .LATCH(CFG_LATCH), .DS_WORDS(DS_WORDS)) u_pmem (
        .clk, .rst_n(sys_rst_n), .clr(cfg_clr), .all_stopped(lane_live == '0),
        .l_req, .l_addr, .l_win, .l_fb, .l_gnt, .l_rdata,
        .d_req(l_dreq), .d_we(l_dwe), .d_addr(l_daddr), .d_wdata(l_dwdata), .d_gnt(l_dgnt), .d_rdata(l_drdata),
        .h_req(pm_pend && !st_busy), .h_we(pm_we), .h_dw(pm_dw), .h_fb(pm_fb), .h_addr(pm_addr),
        .h_wdata(pm_wdata), .h_gnt(pm_gnt), .h_rdata(pm_rdata),
        .t_en(st_m_en), .t_req(st_m_req), .t_we(st_m_we), .t_addr(st_m_addr), .t_wdata(st_m_wdata),
        .cnt(slot_cnt), .frame_end
    );

    logic        lb_en;
    logic [15:0] lb_pat, pad_p, pad_n;
    tse_selftest u_st (
        .clk, .rst_n(sys_rst_n), .start(bist_start), .clr(cfg_clr),
        .m_en(st_m_en), .m_req(st_m_req), .m_we(st_m_we), .m_addr(st_m_addr), .m_wdata(st_m_wdata), .m_rdata(pm_rdata),
        .lb_en, .lb_pat, .pad_p, .pad_n, .busy(st_busy), .done(st_done), .pass(st_pass), .result(st_result)
    );

    // ---------------- chip configuration bank
    logic [64*32-1:0] chip_q;
    logic [31:0]      chip_rdata;
    logic             chip_busy;
    tse_cfg_bank #(.WORDS(64), .LATCH(CFG_LATCH), .MASK(CHIP_MASK)) u_ccfg (
        .clk, .rst_n(sys_rst_n), .clr(cfg_clr),
        .we(h_wr && ccfg.hit), .addr(cw), .wdata(h_wdata),
        .busy(chip_busy), .rd_addr(cw), .rd_data(chip_rdata), .q(chip_q)
    );

    logic [3:0] pin_opt;
    assign pin_opt = chip_q[32*CW_PIN_OPT +: 4];
    assign spi_en  = !pin_opt[tse_regs_pkg::HR_PIN_OPT_HOST_SPI_OFF_LO] && !load_mode;

    // ---------------- lanes and their configuration banks
    logic [31:0]           lane_rd [LANES];
    logic [LANES-1:0]      l_cfg_we, ev_valid, lane_busy;
    logic [LANES*5-1:0]    l_cfg_word;
    logic [LANES*32-1:0]   l_cfg_wdata;
    logic [LANES*4-1:0]    ev_code;
    logic [NI-1:0]         vin_p, vin_n, vout, voe, live_pins;
    logic [31:0]           lane_h_rdata [LANES];
    logic [31:0]           lane_rx [LANES];
    logic [1:0]            lane_flags [LANES];

    for (genvar i = 0; i < LANES; i++) begin : g_lane
        logic hb_we, sel;
        assign sel   = a_lane && hl == 2'(i);
        assign hb_we = h_wr && sel && hcls == tse_regs_pkg::RC_L;
        assign live_pins[V*i +: V] = {V{lane_live[i]}};

        tse_cfg_bank #(.WORDS(32), .LATCH(CFG_LATCH), .MASK(LANE_MASK)) u_cfg (
            .clk, .rst_n(sys_rst_n), .clr(cfg_clr),
            .we(lane_live[i] ? l_cfg_we[i] : hb_we),
            .addr(lane_live[i] ? l_cfg_word[5*i +: 5] : hword),
            .wdata(lane_live[i] ? l_cfg_wdata[32*i +: 32] : h_wdata),
            .busy(lane_busy[i]), .rd_addr(hword), .rd_data(lane_rd[i]),
            .q(lcfg[1024*i +: 1024])
        );

        tse_lane #(.K(2), .PW(16), .V(V)) u_lane (
            .clk, .rst_n(eng_rst_n), .run(lane_live[i]),
            .cfg(lcfg[1024*i +: 1024]), .cfg_we(l_cfg_we[i]), .cfg_word(l_cfg_word[5*i +: 5]),
            .cfg_wdata(l_cfg_wdata[32*i +: 32]),
            .h_we(h_wr && sel && (hcls == tse_regs_pkg::RC_F || hcls == tse_regs_pkg::RC_P)),
            .h_re(h_re && sel && hcls != tse_regs_pkg::RC_L),
            .h_addr(hreg), .h_wdata, .h_rdata(lane_h_rdata[i]),
            .h_tx_we(h_wr && h_addr == tse_regs_pkg::HR_MBOX_TX + 16'(i)), .h_tx_data(h_wdata),
            .h_rx_re(h_re && h_addr == tse_regs_pkg::HR_MBOX_RX + 16'(i)),
            .h_rx_data(lane_rx[i]), .h_flags(lane_flags[i]),
            .ev_valid(ev_valid[i]), .ev_code(ev_code[4*i +: 4]),
            .imem_req(l_req[i]), .imem_addr(l_addr[7*i +: 7]), .imem_gnt(l_gnt[i]), .imem_rdata(l_rdata),
            .vin_p(vin_p[V*i +: V]), .vin_n(vin_n[V*i +: V]), .vout(vout[V*i +: V]), .voe(voe[V*i +: V])
        );
    end

    // ---------------- RUN, interrupts
    logic [3:0]         trig_stop;
    logic [4*LANES-1:0] irq_q;
    always_ff @(posedge clk or negedge eng_rst_n)
        if (!eng_rst_n) begin
            run_q <= '0; lane_live <= '0; irq_q <= '0;
        end else begin
            if (h_wr && h_addr == tse_regs_pkg::HR_RUN) run_q <= h_wdata[3:0];
            if (cfg_clr)                  run_q <= '0;
            else if (trig_stop != '0)     run_q <= run_q & ~trig_stop;
            if (frame_end) lane_live <= run_q[LANES-1:0] & {LANES{loaded}};
            begin
                logic [4*LANES-1:0] set;
                for (int i = 0; i < LANES; i++) set[4*i +: 4] = ev_valid[i] ? ev_code[4*i +: 4] : 4'd0;
                irq_q <= ((h_re && h_addr == tse_regs_pkg::HR_IRQ) ? '0 : irq_q) | set;
            end
        end

    // ---------------- crossbar and sample delay
    logic [16*9-1:0] x_out;
    logic [NI*6-1:0] x_in;
    logic [NI*3-1:0] x_dly;
    logic [NI-1:0]   sink_p;
    logic            trig_level;
    always_comb begin
        for (int p = 0; p < 16; p++) x_out[9*p +: 9] = chip_q[32*(CW_PAD_OUT + p) +: 9];
        for (int i = 0; i < NI; i++) begin
            x_in[6*i +: 6]  = chip_q[32*(CW_LANE_IN + i) +: 6];
            x_dly[3*i +: 3] = chip_q[32*(CW_LANE_IN + i) + tse_regs_pkg::HR_LANE_IN_DELAY_LO +: 3];
        end
    end

    tse_xbar #(.NI(NI)) u_xbar (
        .clk, .rst_n(sys_rst_n), .cfg_out(x_out), .cfg_in(x_in),
        .loopback(pin_opt[tse_regs_pkg::HR_PIN_OPT_LOOPBACK_LO]), .lb_test(lb_en), .lb_pat,
        .ovr_en({8'd0, pin_opt[tse_regs_pkg::HR_PIN_OPT_TRIG_PAD_LO], pin_opt[tse_regs_pkg::HR_PIN_OPT_IRQ_PAD_LO], 6'd0}),
        .ovr_val({8'd0, trig_level, irq_q != '0, 6'd0}),
        .pad_p, .pad_n, .src_out(16'(vout)), .src_oe(16'(voe)), .src_live(16'(live_pins)),
        .sink_p, .sink_n(vin_n), .pin_in(pad_in), .pin_out(pad_out), .pin_oe(pad_oe)
    );

    tse_pin_delay #(.N(NI)) u_dly (
        .clk, .rst_n(sys_rst_n), .cfg_dly(x_dly), .in_p(sink_p), .in_n(vin_n), .out_p(vin_p)
    );

    // ---------------- event unit
    logic [31:0] ev_head, ev_ts;
    logic [15:0] ev_status;
    logic [5:0]  chip_flags;
    logic [4*16-1:0] ev_win;
    logic [LANES*6-1:0] ev_code6;
    always_comb begin
        for (int s = 0; s < 4; s++) ev_win[16*s +: 16] = chip_q[32*(CW_TRIG_WIN + s) +: 16];
        for (int i = 0; i < LANES; i++) ev_code6[6*i +: 6] = {2'd0, ev_code[4*i +: 4]};
    end

    tse_event_unit #(.LANES(LANES), .DEPTH(EVQ_DEPTH)) u_ev (
        .clk, .rst_n(eng_rst_n),
        .cfg_ctl(chip_q[32*CW_EV_CTL +: 10]), .cfg_match(chip_q[32*CW_TRIG_MATCH +: 128]),
        .cfg_win(ev_win), .cfg_trig(chip_q[32*CW_TRIG_CTL +: 17]),
        .ev_valid, .ev_code(ev_code6), .ev_data('0), .cf_we('0), .cf_mask('0), .cf_val('0),
        .pop(h_re && h_addr == tse_regs_pkg::HR_EV_POP),
        .clr_fifo(h_wr && h_addr == tse_regs_pkg::HR_EV_CLEAR && h_wdata[tse_regs_pkg::HR_EV_CLEAR_FIFO_LO]),
        .clr_drop(h_wr && h_addr == tse_regs_pkg::HR_EV_CLEAR && h_wdata[tse_regs_pkg::HR_EV_CLEAR_DROPS_LO]),
        .arm(h_wr && h_addr == tse_regs_pkg::HR_TRIG_ARM && h_wdata[tse_regs_pkg::HR_TRIG_ARM_ARM_LO]),
        .head(ev_head), .status(ev_status), .ts(ev_ts), .chip_flags, .trig_level, .trig_stop
    );

    // ---------------- runtime host SPI
    tse_host_spi u_spi (
        .clk, .rst_n(eng_rst_n), .cs_n(!spi_en || pad_p[0]), .sck(pad_p[1]), .mosi(pad_p[2]),
        .miso(spi_miso), .s_valid, .s_first, .s_data, .dout(ld_dout)
    );

    // ---------------- read data
    always_comb begin
        logic [7:0] mbox;
        mbox = '0;
        for (int i = 0; i < LANES; i++) mbox[2*i +: 2] = lane_flags[i];
        rmux = '0;
        if (a_lane)     rmux = hcls == tse_regs_pkg::RC_L ? lane_rd[hl] : lane_h_rdata[hl];
        else if (ccfg.hit) rmux = chip_rdata;
        else begin
            if (h_addr == tse_regs_pkg::HR_ID)          rmux = {12'd0, 4'(EVQ_DEPTH), CFG_LATCH, 3'(LANES), 4'(tse_isa_pkg::ISA_VERSION), 8'h54};
            if (h_addr == tse_regs_pkg::HR_RUN)         rmux = {28'd0, run_q};
            if (h_addr == tse_regs_pkg::HR_STATUS)      rmux = {20'd0, 4'(lane_live), 4'd0, host_err, st_pass, st_done, loaded};
            if (h_addr == tse_regs_pkg::HR_BIST)        rmux = {18'd0, st_result};
            if (h_addr == tse_regs_pkg::HR_LOAD_CRC)    rmux = load_crc;
            if (h_addr == tse_regs_pkg::HR_IRQ)         rmux = 32'(irq_q);
            if (h_addr == tse_regs_pkg::HR_MBOX_STATUS) rmux = {24'd0, mbox};
            if (h_addr == tse_regs_pkg::HR_EV_STATUS)   rmux = {16'd0, ev_status};
            if (h_addr == tse_regs_pkg::HR_EV_POP)      rmux = ev_head;
            if (h_addr == tse_regs_pkg::HR_TS)          rmux = ev_ts;
            for (int i = 0; i < LANES; i++) if (h_addr == tse_regs_pkg::HR_MBOX_RX + 16'(i)) rmux = lane_rx[i];
        end
    end

    always_ff @(posedge clk or negedge sys_rst_n)
        if (!sys_rst_n) begin
            h_rvalid <= 1'b0; h_rdata <= '0;
        end else begin
            h_rvalid <= pm_rd_q || (h_re && !sram_go);
            if (pm_rd_q)                h_rdata <= pm_rdata;
            else if (h_re && !sram_go)  h_rdata <= h_err ? 32'd0 : rmux;
        end

    wire _unused = &{lane_busy, chip_busy, chip_flags, ld_rst_n, chip_q, slot_cnt, l_dgnt, l_drdata, 1'b0};
endmodule
`default_nettype wire
