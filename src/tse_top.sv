`default_nettype none
// TSE chip core: LANES lanes (3 in 6x4, 4 in 8x4), one SRAM, config banks,
// crossbar, event unit, loader, self-test and runtime SPI.
// Chip config bank (bank 15) words: 0 {spi_en[8], lane_en[3:0]},
// 1..16 output pad p {idle_oe, idle, en, od, inv, src}, 17..20 sink inputs (one byte each,
// 4 per word), 21..24 sink sample delays (one byte each), 25..28 trigger
// stages, 29 trigger length, 31 run key.
// Runtime SPI map: 0x0LRR lane L register RR, 0x1L0x lane L mailbox,
// 0x200x event unit (read of 0x2000 pops), 0x3000 status, 0x4BWW config bank B
// word WW (a lane bank only while that lane is stopped).
module tse_top #(
    parameter int          LANES     = 3,
    parameter bit          CFG_LATCH = 1,
    parameter logic [15:0] KEY       = 16'h7E5A
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
    localparam int NS = NI + 1;           // lane pins plus trigger-out
    localparam int SW = $clog2(NS);

    // Bits that exist in each config bank (to be generated from the config
    // config table). Word 31 is first in each concatenation.
    localparam logic [31:0] PADM = 32'((1 << (SW + 5)) - 1);
    localparam logic [1023:0] LANE_MASK = {
        {8{32'h0}},
        32'h0000_1FFF, 32'h01FF_FFFF, 32'h0000_0000, 32'hFFFF_FFFF,    // rule 1
        32'h0000_1FFF, 32'h01FF_FFFF, 32'h0000_0000, 32'hFFFF_FFFF,    // rule 0
        {2{32'h0}}, 32'h0FC0_0000, 32'h0000_0FFF,                     // WAIT alternate, RULE_CLK
        32'h0007_FFFF, 32'h00FF_FFFF, 32'h03FF_FFFF,                     // timer, lane
        32'h000F_FFFF, 32'h000F_FFFF, 32'h000F_FFFF,                     // DPLL
        32'h0000_0F3F, 32'h0000_007F, {4{32'hFFFF_FFFF}}                // line, CRC
    };
    localparam logic [1023:0] CHIP_MASK = {
        32'h0000_FFFF, 32'h0, 32'h0000_00FF,                             // key, trigger length
        {4{32'h8FFF_FFFF}}, {4{32'h0707_0707}}, {4{32'h1F1F_1F1F}},     // stages, delays, sinks
        {16{PADM}}, 32'h0000_010F                                         // pads, enables
    };

    // ---------------- resets and mode
    logic eng_rst_n, ld_rst_n, sys_rst_n;
    tse_rst_sync u_rst (.clk, .rst_n, .eng_rst_n, .ld_rst_n, .sys_rst_n, .load_mode);

    // ---------------- chip config bank
    logic [1023:0] chip;
    logic [31:0]   chip_rd;
    logic          key_ok;
    logic          chip_busy, pm_h_gnt;
    logic [LANES-1:0] lane_busy;           // unused: write spacing is guaranteed by construction
    logic [3:0]    lane_en;
    assign key_ok  = chip[32*31 +: 16] == KEY;
    assign lane_en = chip[3:0];
    assign spi_en  = chip[8] && !load_mode;

    // loader, self-test and host SPI buses
    logic        ld_p_req, ld_p_we, ld_c_we, ld_c_clr, st_start, st_busy, st_done, st_pass;
    logic [8:0]  ld_p_addr;
    logic [31:0] ld_p_wdata, ld_c_wdata, ld_c_rdata, st_info, pm_h_rdata;
    logic [3:0]  ld_c_bank;
    logic [4:0]  ld_c_word;
    logic        hs_we, hs_re;
    logic [15:0] hs_addr;
    logic [31:0] hs_wdata, hs_rdata;
    logic        hs_cfg_we;
    assign hs_cfg_we = hs_we && hs_addr[15:12] == 4'd4;

    tse_cfg_bank #(.WORDS(32), .LATCH(CFG_LATCH), .MASK(CHIP_MASK)) u_chip_cfg (
        .clk, .rst_n(sys_rst_n), .clr(ld_c_clr),
        .we(load_mode ? (ld_c_we && ld_c_bank == 4'hF) : (hs_cfg_we && hs_addr[11:8] == 4'hF)),
        .addr(load_mode ? ld_c_word : hs_addr[4:0]),
        .wdata(load_mode ? ld_c_wdata : hs_wdata), .busy(chip_busy),
        .rd_addr(load_mode ? ld_c_word : hs_addr[4:0]), .rd_data(chip_rd), .q(chip)
    );

    // ---------------- lanes and their config banks
    logic [LANES*1024-1:0] lcfg;
    logic [31:0]           lane_rd [LANES];
    logic [LANES-1:0]      l_cfg_we, l_req, l_gnt, ev_valid;
    logic [LANES*5-1:0]    l_cfg_word;
    logic [LANES*32-1:0]   l_cfg_wdata;
    logic [LANES*7-1:0]    l_addr;
    logic [LANES-1:0]      lane_live;          // registered run: one cycle at idle after reset
    logic [NI-1:0]         live_pins;
    always_ff @(posedge clk or negedge eng_rst_n)
        if (!eng_rst_n) lane_live <= '0;
        else            lane_live <= {LANES{key_ok}} & lane_en[LANES-1:0];
    logic [LANES*4-1:0]    ev_code;
    logic [31:0]           l_rdata;
    logic [NI-1:0]         vin_p, vin_n, vout, voe;
    logic [31:0]           lane_h_rdata [LANES];
    logic [31:0]           lane_rx [LANES];
    logic [1:0]            lane_flags [LANES];

    for (genvar i = 0; i < LANES; i++) begin : g_lane
        logic sel_ld, sel_hs, own;
        assign sel_ld = ld_c_bank == 4'(i);
        assign sel_hs = hs_addr[11:8] == 4'(i);
        assign own    = lane_en[i];                      // a running lane owns its bank
        assign live_pins[V*i +: V] = {V{lane_live[i]}};

        tse_cfg_bank #(.WORDS(32), .LATCH(CFG_LATCH), .MASK(LANE_MASK)) u_cfg (
            .clk, .rst_n(sys_rst_n), .clr(ld_c_clr),
            .we(load_mode ? (ld_c_we && sel_ld) : own ? l_cfg_we[i] : (hs_cfg_we && sel_hs)),
            .addr(load_mode ? ld_c_word : own ? l_cfg_word[5*i +: 5] : hs_addr[4:0]),
            .wdata(load_mode ? ld_c_wdata : own ? l_cfg_wdata[32*i +: 32] : hs_wdata),
            .busy(lane_busy[i]), .rd_addr(load_mode ? ld_c_word : hs_addr[4:0]), .rd_data(lane_rd[i]),
            .q(lcfg[1024*i +: 1024])
        );

        tse_lane #(.K(2), .PW(16), .V(V)) u_lane (
            .clk, .rst_n(eng_rst_n), .run(lane_live[i]),
            .cfg(lcfg[1024*i +: 1024]), .cfg_we(l_cfg_we[i]), .cfg_word(l_cfg_word[5*i +: 5]),
            .cfg_wdata(l_cfg_wdata[32*i +: 32]),
            .h_we(hs_we && hs_addr[15:12] == 4'd0 && hs_addr[9:8] == 2'(i)),
            .h_re(hs_re && hs_addr[15:12] == 4'd0 && hs_addr[9:8] == 2'(i)),
            .h_addr(hs_addr[7:0]), .h_wdata(hs_wdata), .h_rdata(lane_h_rdata[i]),
            .h_tx_we(hs_we && hs_addr[15:12] == 4'd1 && hs_addr[9:8] == 2'(i) && !hs_addr[0]),
            .h_tx_data(hs_wdata),
            .h_rx_re(hs_re && hs_addr[15:12] == 4'd1 && hs_addr[9:8] == 2'(i) && !hs_addr[0]),
            .h_rx_data(lane_rx[i]), .h_flags(lane_flags[i]),
            .ev_valid(ev_valid[i]), .ev_code(ev_code[4*i +: 4]),
            .imem_req(l_req[i]), .imem_addr(l_addr[7*i +: 7]), .imem_gnt(l_gnt[i]), .imem_rdata(l_rdata),
            .vin_p(vin_p[V*i +: V]), .vin_n(vin_n[V*i +: V]), .vout(vout[V*i +: V]), .voe(voe[V*i +: V])
        );
    end

    // ---------------- program memory (fixed slots), loader or self-test port
    logic       st_m_req, st_m_we;
    logic [8:0] st_m_addr;
    logic [31:0] st_m_wdata;
    tse_prog_mem #(.LANES(LANES)) u_pmem (
        .clk, .rst_n(sys_rst_n), .run(!load_mode),
        .l_req, .l_addr, .l_gnt, .l_rdata,
        .h_req(st_busy ? st_m_req : ld_p_req), .h_we(st_busy ? st_m_we : ld_p_we),
        .h_addr(st_busy ? st_m_addr : ld_p_addr), .h_wdata(st_busy ? st_m_wdata : ld_p_wdata),
        .h_gnt(pm_h_gnt), .h_rdata(pm_h_rdata)
    );

    // ---------------- loader and self-test (load mode only)
    always_comb begin
        ld_c_rdata = '0;
        if (ld_c_bank == 4'hF) ld_c_rdata = chip_rd;
        for (int i = 0; i < LANES; i++) if (ld_c_bank == 4'(i)) ld_c_rdata = lane_rd[i];
    end

    tse_loader #(.KEY(KEY)) u_loader (
        .clk, .rst_n(ld_rst_n), .din(pad_in[7:0]), .dout(ld_dout),
        .p_req(ld_p_req), .p_we(ld_p_we), .p_addr(ld_p_addr), .p_wdata(ld_p_wdata), .p_rdata(pm_h_rdata),
        .c_we(ld_c_we), .c_clr(ld_c_clr), .c_bank(ld_c_bank), .c_word(ld_c_word),
        .c_wdata(ld_c_wdata), .c_rdata(ld_c_rdata),
        .st_start, .st_busy, .st_done, .st_pass, .st_info, .key_ok
    );

    logic        lb_en;
    logic [15:0] lb_pat, pad_p, pad_n;
    tse_selftest u_st (
        .clk, .rst_n(ld_rst_n), .start(st_start),
        .m_req(st_m_req), .m_we(st_m_we), .m_addr(st_m_addr), .m_wdata(st_m_wdata), .m_rdata(pm_h_rdata),
        .lb_en, .lb_pat, .pad_p, .pad_n, .busy(st_busy), .done(st_done), .pass(st_pass), .info(st_info)
    );

    // ---------------- crossbar and sample delay
    logic        trig_out;
    logic [NI-1:0] sink_p, sink_n;
    logic [16*(SW+5)-1:0] x_out;
    logic [NI*5-1:0]      x_in;
    logic [NI*3-1:0]      x_dly;
    always_comb begin
        for (int p = 0; p < 16; p++) x_out[(SW+5)*p +: SW+5] = chip[32*(1+p) +: SW+5];
        for (int i = 0; i < NI; i++) begin
            x_in[5*i +: 5]  = chip[32*(17 + i/4) + 8*(i%4) +: 5];
            x_dly[3*i +: 3] = chip[32*(21 + i/4) + 8*(i%4) +: 3];
        end
    end

    tse_xbar #(.NS(NS), .NI(NI), .P(16), .PO(16)) u_xbar (
        .clk, .rst_n(sys_rst_n), .cfg_out(x_out), .cfg_in(x_in), .lb_en, .lb_pat, .pad_p, .pad_n,
        .src_out({trig_out, vout}), .src_oe({1'b1, voe}), .src_live({key_ok, live_pins}),
        .sink_p, .sink_n,
        .pin_in(pad_in), .pin_out(pad_out), .pin_oe(pad_oe)
    );

    tse_pin_delay #(.N(NI), .D(8)) u_dly (
        .clk, .rst_n(sys_rst_n), .cfg_dly(x_dly), .in_p(sink_p), .in_n(sink_n),
        .out_p(vin_p), .out_n(vin_n)
    );

    // ---------------- event unit
    logic [31:0] ev_head, ev_ts, ev_trig_ts;
    logic        ev_empty, ev_ovf;
    tse_event_unit #(.LANES(LANES), .DEPTH(8), .STAGES(4)) u_ev (
        .clk, .rst_n(eng_rst_n), .run(key_ok), .cfg_stage(chip[32*25 +: 128]),
        .cfg_trig_len(chip[32*29 +: 8]), .ev_valid, .ev_code,
        .pop(hs_re && hs_addr == 16'h2000), .head(ev_head), .empty(ev_empty), .overflow(ev_ovf),
        .ts(ev_ts), .trig_ts(ev_trig_ts), .trig_out
    );

    wire _unused = &{chip_busy, lane_busy, pm_h_gnt, chip[1023:32*29+8], 1'b0};

    // ---------------- runtime host SPI
    tse_host_spi u_spi (
        .clk, .rst_n(eng_rst_n), .cs_n(!spi_en || pad_p[0]), .sck(pad_p[1]), .mosi(pad_p[2]),
        .miso(spi_miso), .h_we(hs_we), .h_re(hs_re), .h_addr(hs_addr), .h_wdata(hs_wdata),
        .h_rdata(hs_rdata)
    );

    always_comb begin
        logic [1:0] l;
        l = hs_addr[9:8];
        hs_rdata = '0;
        unique case (hs_addr[15:12])
            4'd0: if (32'(l) < LANES) hs_rdata = lane_h_rdata[l];
            4'd1: if (32'(l) < LANES) hs_rdata = hs_addr[0] ? {30'd0, lane_flags[l]} : lane_rx[l];
            4'd2: unique case (hs_addr[1:0])
                            2'd0:    hs_rdata = ev_head;
                            2'd1:    hs_rdata = ev_ts;
                            2'd2:    hs_rdata = ev_trig_ts;
                            default: hs_rdata = {30'd0, ev_ovf, ev_empty};
                        endcase
            4'd3: hs_rdata = {27'd0, key_ok, lane_en};
            4'd4: if (hs_addr[11:8] == 4'hF) hs_rdata = chip_rd;
                        else if (32'(hs_addr[11:8]) < LANES) hs_rdata = lane_rd[hs_addr[9:8]];
            default: ;
        endcase
    end
endmodule
`default_nettype wire
