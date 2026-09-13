`default_nettype none
// One TSE lane: lane with configuration read from the lane's config
// bank (cfg, 32 words of 32 bits) instead of local registers.
// Config words: 0 CRC poly, 1 init, 2 xorout, 3 check, 4 {refout, refin, wm1}
//   5 {codec[11:8], stuffer mode[5:4], n[3:0]}, 6 DPLL period, 7 sample point,
//   8 {DPLL flags[19:16], sjw[15:0]}, 9 lane config, 10 timer increment,
//   11 timer {ev0_sel[18], en[17], on_tick[16], reload[15:0]},
//   12 RULE_CLK {tick_delay[11:4], edge[3], clk_pin[2:1], src[0]},
//   13 WAIT alternate {alt_idx[27:25], alt_src[24:23], alt_pol[22]} (LANE_CFG bits),
//   16 + 4k rule k {mask, pattern}, 18 + 4k {any[24], pos[23:16], resp[15:0]}, 19 + 4k control.
// Sequencer register space (CSRW/CSRR address): 0x00-0x1f config words
// (write-only, through the bank, 3 cycles), 0x20 TXDATA, 0x21 TXCTL, 0x22 RXDATA,
// 0x23 STATUS, 0x24 CMD, 0x25 CRC result.
module tse_lane #(
    parameter int K  = 2,
    parameter int PW = 16,
    parameter int V  = 4
) (
    input  logic         clk,
    input  logic         rst_n,
    input  logic         run,
    input  logic [1023:0] cfg,
    output logic         cfg_we,
    output logic [4:0]   cfg_word,
    output logic [31:0]  cfg_wdata,
    input  logic         h_we,
    input  logic         h_re,
    input  logic [7:0]   h_addr,
    input  logic [31:0]  h_wdata,
    output logic [31:0]  h_rdata,
    input  logic         h_tx_we,
    input  logic [31:0]  h_tx_data,
    input  logic         h_rx_re,
    output logic [31:0]  h_rx_data,
    output logic [1:0]   h_flags,
    output logic         ev_valid,
    output logic [3:0]   ev_code,
    output logic         imem_req,
    output logic [6:0]   imem_addr,
    input  logic         imem_gnt,
    input  logic [31:0]  imem_rdata,
    input  logic [V-1:0] vin_p,
    input  logic [V-1:0] vin_n,
    output logic [V-1:0] vout,
    output logic [V-1:0] voe
);
    logic [31:0] cw [32];                              // config words
    always_comb for (int i = 0; i < 32; i++) cw[i] = cfg[32*i +: 32];

    logic        s_we, s_re, s_cmd_we;
    logic [7:0]  s_addr, s_cmd;
    logic [31:0] s_wdata, rdata;
    logic [7:0]  ev, pins, dirs, vp8, vn8;
    logic [3:0]  irq;
    assign vp8 = 8'(vin_p);
    assign vn8 = 8'(vin_n);

    // runtime registers: host or sequencer; config words: sequencer to the bank
    logic        we;
    logic [7:0]  wa;
    logic [31:0] wd;
    assign we        = h_we | s_we;
    assign wa        = h_we ? h_addr : s_addr;
    assign wd        = h_we ? h_wdata : s_wdata;
    assign cfg_we    = s_we && s_addr[7:5] == 3'd0;
    assign cfg_word  = s_addr[4:0];
    assign cfg_wdata = s_wdata;

    logic [25:0] lcfg;
    logic [5:0]  txctl;
    logic [7:0]  cmd;
    logic        cmd_v;
    assign lcfg  = cw[9][25:0];
    assign cmd_v = (we && wa == 8'h24) || s_cmd_we;
    assign cmd   = s_cmd_we ? s_cmd : wd[7:0];

    logic frame_rst, crc_clear, hard_arm, tx_abort_c, line_set, line_val;
    assign frame_rst  = cmd_v && cmd[0];
    assign crc_clear  = cmd_v && cmd[1];
    assign hard_arm   = cmd_v && cmd[2];
    assign tx_abort_c = cmd_v && cmd[3];

    logic [2:0] txpin, rxpin;
    assign txpin = lcfg[2:0];
    assign rxpin = lcfg[5:3];

    logic bit_start, half, q1, sample, samp_val, edge_seen, dpll_en;
    tse_dpll u_dpll (
        .clk, .rst_n, .cfg_period(cw[6][19:0]), .cfg_spt(cw[7][19:0]), .cfg_sjw(cw[8][15:0]),
        .cfg_resync(cw[8][16]), .cfg_dual(cw[8][17]), .cfg_rise(cw[8][18]), .cfg_fall(cw[8][19]),
        .enable(dpll_en), .hard_arm, .s_p(vp8[rxpin]), .s_n(vn8[rxpin]),
        .bit_start, .half, .q1, .sample, .samp_val, .edge_seen
    );

    logic        crc_bv, crc_bin, crc_busy, crc_load, crc_shift, crc_txb, crc_ok;
    logic [31:0] crc_res;
    tse_crc u_crc (
        .clk, .rst_n, .cfg_poly(cw[0]), .cfg_init(cw[1]), .cfg_xo(cw[2]), .cfg_check(cw[3]),
        .cfg_wm1(cw[4][4:0]), .cfg_refin(cw[4][5]), .cfg_refout(cw[4][6]),
        .clear(crc_clear), .bit_valid(crc_bv), .bit_in(crc_bin), .byte_valid(1'b0), .byte_in(8'd0),
        .busy(crc_busy), .tx_load(crc_load), .tx_shift(crc_shift), .tx_bit(crc_txb),
        .result(crc_res), .check_ok(crc_ok)
    );

    logic st_step, st_din, st_take, st_dout, st_pend, rx_v, rx_b, rx_serr;
    logic cd_rxv, cd_rxb, cd_rxerr, line;
    tse_stuffer u_stuff (
        .clk, .rst_n, .cfg_n(cw[5][3:0]), .cfg_mode(cw[5][5:4]), .clear(frame_rst),
        .tx_step(st_step), .tx_din(st_din), .tx_take(st_take), .tx_dout(st_dout), .tx_pending(st_pend),
        .rx_step(cd_rxv), .rx_din(cd_rxb), .rx_valid(rx_v), .rx_dout(rx_b), .rx_err(rx_serr)
    );

    logic cd_step, cd_bit, tx_active;
    tse_codec u_codec (
        .clk, .rst_n, .cfg_mode(cw[5][9:8]), .cfg_inv(cw[5][10]), .cfg_t_on(cw[5][11]),
        .tx_step(cd_step), .tx_half(half && tx_active), .tx_bit(cd_bit),
        .line_set, .line_val, .tx_line(line),
        .rx_sample(sample), .rx_s(samp_val), .rx_q1(q1), .rx_q1_s(vp8[rxpin]),
        .rx_valid(cd_rxv), .rx_bit(cd_rxb), .rx_err(cd_rxerr)
    );

    logic [K*PW-1:0] r_pat, r_msk;
    logic [K*9-1:0]  r_pos;
    logic [K*16-1:0] r_resp;
    logic [K*13-1:0] r_ctl;
    always_comb
        for (int k = 0; k < K; k++) begin
            r_pat[PW*k +: PW]  = cw[16 + 4*k][PW-1:0];
            r_msk[PW*k +: PW]  = cw[16 + 4*k][16 +: PW];
            r_pos[9*k +: 9]    = cw[18 + 4*k][24:16];
            r_resp[16*k +: 16] = cw[18 + 4*k][15:0];
            r_ctl[13*k +: 13]  = cw[19 + 4*k][12:0];
        end

    logic drv_en, drv_val, hit_p, abort_p;
    logic [K-1:0] hits;
    tse_resp_rule #(.K(K), .PW(PW)) u_rule (
        .clk, .rst_n, .cfg_pat(r_pat), .cfg_msk(r_msk), .cfg_pos(r_pos), .cfg_resp(r_resp),
        .cfg_ctl(r_ctl), .cfg_src(cw[12][0]), .cfg_edge(cw[12][3]), .cfg_tdly(cw[12][11:4]), .frame_rst,
        .bit_valid(rx_v), .bit_in(rx_b), .bit_start, .pin_clk(vp8[{1'b0, cw[12][2:1]}]), .pin_dat(vp8[rxpin]), .crc_ok,
        .ext_flag(!tx_active), .flags_clr(frame_rst), .drv_en, .drv_val, .hit_flags(hits),
        .hit_pulse(hit_p), .abort_pulse(abort_p)
    );

    // TX engine
    logic [31:0] txsr;
    logic [5:0]  tcnt, ccnt;
    logic        app_crc, load_pend, data_left, crc_ph, tx_step, arb_lost, abort_any, tx_fin, tx_feed;
    assign crc_ph    = tcnt == 6'd0 && ccnt != 6'd0;
    assign data_left = tcnt != 6'd0 || load_pend || ccnt != 6'd0;
    assign tx_step   = bit_start && tx_active;
    assign st_step   = tx_step && (data_left || st_pend);
    assign st_din    = crc_ph ? crc_txb : (lcfg[13] ? txsr[31] : txsr[0]);
    assign crc_load  = load_pend;
    assign crc_shift = st_take && crc_ph;
    assign arb_lost  = lcfg[17] && tx_active && sample && line && !samp_val;
    assign abort_any = tx_abort_c || arb_lost || abort_p;
    assign cd_step   = st_step || (lcfg[18] && drv_en && bit_start);
    assign cd_bit    = (lcfg[18] && drv_en) ? drv_val : st_dout;
    assign tx_fin    = tx_step && !data_left && !st_pend;
    assign line_set  = !run || (cmd_v && cmd[6]) || arb_lost || tx_fin;   // stopped: idle level
    assign line_val  = (!run || arb_lost || tx_fin) ? lcfg[25] : cmd[7];
    assign tx_feed   = st_take && tcnt != 6'd0;
    assign crc_bv    = (lcfg[15] && tx_feed) || (lcfg[16] && !lcfg[15] && rx_v);
    assign crc_bin   = lcfg[15] ? st_din : rx_b;

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            txsr <= '0; tcnt <= '0; ccnt <= '0; app_crc <= 1'b0; load_pend <= 1'b0; tx_active <= 1'b0;
        end else if (abort_any) begin
            tcnt <= '0; ccnt <= '0; load_pend <= 1'b0; tx_active <= 1'b0;
        end else if (we && wa == 8'h20) begin
            txsr <= wd; tcnt <= {1'b0, txctl[4:0]} + 6'd1; app_crc <= txctl[5]; tx_active <= 1'b1;
        end else begin
            load_pend <= 1'b0;
            if (load_pend) ccnt <= {1'b0, cw[4][4:0]} + 6'd1;
            if (tx_feed) begin
                txsr <= lcfg[13] ? {txsr[30:0], 1'b0} : {1'b0, txsr[31:1]};
                tcnt <= tcnt - 6'd1;
                if (tcnt == 6'd1 && app_crc) load_pend <= 1'b1;
            end
            if (crc_shift) ccnt <= ccnt - 6'd1;
            if (tx_fin) tx_active <= 1'b0;
        end

    // RX word assembly and sticky flags
    logic [31:0] rxsr;
    logic [4:0]  rcnt;
    logic        rx_full, serr_s, abort_s, rd_rx;
    assign rd_rx = (h_re && h_addr == 8'h22) || (s_re && s_addr == 8'h22);

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            rxsr <= '0; rcnt <= '0; rx_full <= 1'b0; serr_s <= 1'b0; abort_s <= 1'b0;
            txctl <= '0; dpll_en <= 1'b0;
        end else begin
            if (we && wa == 8'h21) txctl <= wd[5:0];
            if (cmd_v && cmd[4]) dpll_en <= 1'b1;
            if (cmd_v && cmd[5]) dpll_en <= 1'b0;
            if (rd_rx) rx_full <= 1'b0;
            if (frame_rst) begin
                rcnt <= '0; serr_s <= 1'b0; abort_s <= 1'b0;
            end else begin
                if (rx_v) begin
                    rxsr <= lcfg[14] ? {rxsr[30:0], rx_b} : {rx_b, rxsr[31:1]};
                    if (rcnt == lcfg[24:20]) begin
                        rcnt <= '0; rx_full <= 1'b1;
                    end else rcnt <= rcnt + 5'd1;
                end
                if (rx_serr || cd_rxerr) serr_s <= 1'b1;
                if (arb_lost || abort_p) abort_s <= 1'b1;
            end
        end

    // timer: word 10 increment, word 11 {ev0_sel[18], en[17], on_tick[16], reload};
    // restarts on frame reset; its flag replaces event 0 when ev0_sel is set
    logic        tmr_exp, tmr_flag, tmr_tick;
    logic [15:0] tmr_count;
    tse_timer u_tmr (
        .clk, .rst_n, .cfg_inc(cw[10][23:0]), .cfg_reload(cw[11][15:0]), .cfg_on_tick(cw[11][16]),
        .cfg_auto(1'b0), .en(cw[11][17]), .start(frame_rst), .tick(tmr_tick), .expired(tmr_exp), .count(tmr_count)
    );
    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n)         tmr_flag <= 1'b0;
        else if (frame_rst) tmr_flag <= 1'b0;
        else if (tmr_exp)   tmr_flag <= 1'b1;

    assign ev = {abort_s, !tx_active, bit_start, serr_s, crc_ok, |hits, rx_full,
                             cw[11][18] ? tmr_flag : !data_left};

    logic [31:0] status;
    logic s_aflag, s_tflag;
    assign status = {8'd0, tmr_flag, s_aflag, {(6 - K){1'b0}}, hits, 3'd0, rcnt, tx_active, crc_busy, edge_seen, hit_p, ev[3:0]};
    always_comb begin
        case (s_addr)
            8'h22:   rdata = rxsr;
            8'h23:   rdata = status;
            8'h25:   rdata = crc_res;
            default: rdata = 32'd0;          // config words are write-only for programs
        endcase
        case (h_addr)
            8'h22:   h_rdata = rxsr;
            8'h23:   h_rdata = status;
            8'h25:   h_rdata = crc_res;
            default: h_rdata = 32'd0;
        endcase
    end

    tse_seq u_seq (
        .clk, .rst_n, .run, .imem_req, .imem_addr, .imem_gnt, .imem_rdata,
        .alt_cfg(cw[13][27:22]), .flg({1'b1, 1'b0, !h_flags[1], h_flags[0], edge_seen, half, sample, 1'b0}),
        .pin_in(vp8), .pin_out(pins), .pin_oe(dirs),
        .side_en(lcfg[9]), .ss_base(lcfg[8:6]), .jmp_pin(lcfg[12:10]), .ev,
        .csr_we(s_we), .csr_re(s_re), .csr_addr(s_addr), .csr_wdata(s_wdata), .csr_rdata(rdata),
        .cmd_we(s_cmd_we), .cmd(s_cmd),
        .h_tx_we, .h_tx_data, .h_tx_full(h_flags[0]), .h_rx_re, .h_rx_data, .h_rx_full(h_flags[1]), .aflag_o(s_aflag), .tflag_o(s_tflag), .irq
    );
    assign ev_valid = |irq;
    assign ev_code  = irq;

    logic [7:0] o8, e8;
    always_comb begin
        o8 = pins;
        e8 = dirs;
        if (lcfg[19]) begin
            o8[txpin] = line; e8[txpin] = 1'b1;
        end
        if (drv_en && !lcfg[18]) begin
            o8[txpin] = drv_val; e8[txpin] = 1'b1;
        end
    end
    assign vout = o8[V-1:0];
    wire _unused = &{o8, e8, tmr_tick, tmr_count, s_tflag, 1'b0};
    assign voe  = e8[V-1:0];
endmodule
`default_nettype wire
