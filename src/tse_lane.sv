`default_nettype none
// One TSE lane: sequencer, assists and response rules, configured from the
// lane's bank (cfg, 32 words of 32 bits). Register addresses, bank words and
// field positions come from tse_regs_pkg: a class-L register r lives in bank
// word LW_r, and CSRW to it goes through the bank (3 cycles). Runtime
// registers (TXDATA, TXCTL, RXDATA, STATUS, CMD, CRC_RESULT) are local.
// Programs cannot read configuration: CSRR of a class-L address reads 0.
module tse_lane #(
    parameter int K  = 2,
    parameter int PW = 16,
    parameter int V  = 4,
    parameter bit LATCH = 0
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
    output logic [5:0]   ev_code,
    output logic [7:0]   ev_data,
    output logic [3:0]   irq,
    output logic         halted,
    output logic         sig_we,
    output logic [3:0]   sig_mask,
    output logic [3:0]   sig_val,
    output logic         imem_req,
    output logic [6:0]   imem_addr,
    input  logic         imem_gnt,
    input  logic [31:0]  imem_rdata,
    output logic         dmem_req,
    output logic         dmem_we,
    output logic [6:0]   dmem_addr,
    output logic [31:0]  dmem_wdata,
    input  logic         dmem_gnt,
    input  logic [31:0]  dmem_rdata,
    input  logic [5:0]   chip_flags,
    input  logic [31:0]  ts,
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
    logic [7:0]  ev;
    logic [3:0]  pins, dirs, pwr, vp, vn;
    assign vp = 4'(vin_p);
    assign vn = 4'(vin_n);

    // runtime registers: host or sequencer; config words: sequencer to the bank
    logic        we;
    logic [7:0]  wa;
    logic [31:0] wd;
    assign we        = h_we | s_we;
    assign wa        = h_we ? h_addr : s_addr;
    assign wd        = h_we ? h_wdata : s_wdata;
    assign cfg_we    = s_we && tse_regs_pkg::lane_reg_class(s_addr) == tse_regs_pkg::RC_L;
    assign cfg_word  = tse_regs_pkg::lane_reg_word(s_addr);
    assign cfg_wdata = s_wdata;

    logic [31:0] lcfg, crc_ctl, stuff, codec, dctl, tctl, rclk;
    logic [5:0]  txctl;
    logic [7:0]  cmd;
    logic        cmd_v;
    assign lcfg    = cw[tse_regs_pkg::LW_LANE_CFG];
    assign crc_ctl = cw[tse_regs_pkg::LW_CRC_CTL];
    assign stuff   = cw[tse_regs_pkg::LW_STUFF_CFG];
    assign codec   = cw[tse_regs_pkg::LW_CODEC_CFG];
    assign dctl    = cw[tse_regs_pkg::LW_DPLL_CTL];
    assign tctl    = cw[tse_regs_pkg::LW_TIMER_CTL];
    assign rclk    = cw[tse_regs_pkg::LW_RULE_CLK];
    assign cmd_v   = (we && wa == tse_regs_pkg::LR_CMD) || s_cmd_we;
    assign cmd     = s_cmd_we ? s_cmd : wd[7:0];

    // LANE_CFG fields
    logic [1:0] txpin, rxpin, ss_base, jmp_pin;
    logic       side_en, tx_msb, rx_msb, tx_crc, rx_crc, arb_en, resp_coded, tx_owns_pin, idle;
    logic [4:0] rx_len_m1;
    logic [5:0] alt_cfg;
    assign txpin       = lcfg[tse_regs_pkg::LR_LANE_CFG_TXPIN_HI:tse_regs_pkg::LR_LANE_CFG_TXPIN_LO];
    assign rxpin       = lcfg[tse_regs_pkg::LR_LANE_CFG_RXPIN_HI:tse_regs_pkg::LR_LANE_CFG_RXPIN_LO];
    assign ss_base     = lcfg[tse_regs_pkg::LR_LANE_CFG_SS_BASE_HI:tse_regs_pkg::LR_LANE_CFG_SS_BASE_LO];
    assign side_en     = lcfg[tse_regs_pkg::LR_LANE_CFG_SIDE_EN_HI];
    assign jmp_pin     = lcfg[tse_regs_pkg::LR_LANE_CFG_JMP_PIN_HI:tse_regs_pkg::LR_LANE_CFG_JMP_PIN_LO];
    assign tx_msb      = lcfg[tse_regs_pkg::LR_LANE_CFG_TX_MSB_HI];
    assign rx_msb      = lcfg[tse_regs_pkg::LR_LANE_CFG_RX_MSB_HI];
    assign tx_crc      = lcfg[tse_regs_pkg::LR_LANE_CFG_TX_CRC_HI];
    assign rx_crc      = lcfg[tse_regs_pkg::LR_LANE_CFG_RX_CRC_HI];
    assign arb_en      = lcfg[tse_regs_pkg::LR_LANE_CFG_ARB_EN_HI];
    assign resp_coded  = lcfg[tse_regs_pkg::LR_LANE_CFG_RESP_CODED_HI];
    assign tx_owns_pin = lcfg[tse_regs_pkg::LR_LANE_CFG_TX_OWNS_PIN_HI];
    assign rx_len_m1   = lcfg[tse_regs_pkg::LR_LANE_CFG_RX_LEN_M1_HI:tse_regs_pkg::LR_LANE_CFG_RX_LEN_M1_LO];
    assign idle        = lcfg[tse_regs_pkg::LR_LANE_CFG_IDLE_HI];
    assign alt_cfg     = lcfg[tse_regs_pkg::LR_LANE_CFG_ALT_IDX_HI:tse_regs_pkg::LR_LANE_CFG_ALT_POL_LO];

    logic frame_rst, crc_clear, hard_arm, tx_abort_c, line_set, line_val;
    assign frame_rst  = cmd_v && cmd[tse_regs_pkg::LR_CMD_FRAME_RST_HI];
    assign crc_clear  = cmd_v && cmd[tse_regs_pkg::LR_CMD_CRC_CLEAR_HI];
    assign hard_arm   = cmd_v && cmd[tse_regs_pkg::LR_CMD_HARD_ARM_HI];
    assign tx_abort_c = cmd_v && cmd[tse_regs_pkg::LR_CMD_TX_ABORT_HI];

    logic bit_start, half, q1, sample, samp_val, edge_seen, synced, dpll_en;
    tse_dpll u_dpll (
        .clk, .rst_n, .cfg_period(cw[tse_regs_pkg::LW_DPLL_PERIOD][19:0]),
        .cfg_spt(cw[tse_regs_pkg::LW_DPLL_SPT][19:0]), .cfg_sjw(cw[tse_regs_pkg::LW_DPLL_SJW][15:0]),
        .cfg_resync(dctl[tse_regs_pkg::LR_DPLL_CTL_RESYNC_HI]), .cfg_dual(dctl[tse_regs_pkg::LR_DPLL_CTL_DUAL_HI]),
        .cfg_rise(dctl[tse_regs_pkg::LR_DPLL_CTL_RISE_HI]), .cfg_fall(dctl[tse_regs_pkg::LR_DPLL_CTL_FALL_HI]),
        .enable(dpll_en), .hard_arm, .s_p(vp[rxpin]), .s_n(vn[rxpin]),
        .bit_start, .half, .q1, .sample, .samp_val, .edge_seen, .synced
    );

    logic        crc_bv, crc_bin, crc_busy, crc_load, crc_shift, crc_txb, crc_ok;
    logic [31:0] crc_res;
    logic [4:0]  crc_wm1;
    assign crc_wm1 = crc_ctl[tse_regs_pkg::LR_CRC_CTL_WM1_HI:tse_regs_pkg::LR_CRC_CTL_WM1_LO];
    tse_crc u_crc (
        .clk, .rst_n, .cfg_poly(cw[tse_regs_pkg::LW_CRC_POLY]), .cfg_init(cw[tse_regs_pkg::LW_CRC_INIT]),
        .cfg_xo(cw[tse_regs_pkg::LW_CRC_XOR]), .cfg_check(cw[tse_regs_pkg::LW_CRC_CHECK]),
        .cfg_wm1(crc_wm1), .cfg_refin(crc_ctl[tse_regs_pkg::LR_CRC_CTL_REFIN_HI]),
        .cfg_refout(crc_ctl[tse_regs_pkg::LR_CRC_CTL_REFOUT_HI]),
        .clear(crc_clear), .bit_valid(crc_bv), .bit_in(crc_bin), .byte_valid(1'b0), .byte_in(8'd0),
        .busy(crc_busy), .tx_load(crc_load), .tx_shift(crc_shift), .tx_bit(crc_txb),
        .result(crc_res), .check_ok(crc_ok)
    );

    logic st_step, st_din, st_take, st_dout, st_pend, rx_v, rx_b, rx_serr;
    logic cd_rxv, cd_rxb, cd_rxerr, line;
    tse_stuffer u_stuff (
        .clk, .rst_n, .cfg_n(stuff[tse_regs_pkg::LR_STUFF_CFG_N_HI:tse_regs_pkg::LR_STUFF_CFG_N_LO]),
        .cfg_mode({stuff[tse_regs_pkg::LR_STUFF_CFG_ZEROS_HI], stuff[tse_regs_pkg::LR_STUFF_CFG_ONES_HI]}),
        .clear(frame_rst),
        .tx_step(st_step), .tx_din(st_din), .tx_take(st_take), .tx_dout(st_dout), .tx_pending(st_pend),
        .rx_step(cd_rxv), .rx_din(cd_rxb), .rx_valid(rx_v), .rx_dout(rx_b), .rx_err(rx_serr)
    );

    logic cd_step, cd_bit, tx_active, tx_begun;
    tse_codec u_codec (
        .clk, .rst_n, .cfg_mode(codec[tse_regs_pkg::LR_CODEC_CFG_MODE_HI:tse_regs_pkg::LR_CODEC_CFG_MODE_LO]),
        .cfg_inv(codec[tse_regs_pkg::LR_CODEC_CFG_INV_HI]), .cfg_t_on(codec[tse_regs_pkg::LR_CODEC_CFG_T_ON1_HI]),
        .tx_step(cd_step), .tx_half(half && tx_active && tx_begun), .tx_bit(cd_bit),
        .line_set, .line_val, .tx_line(line),
        .rx_sample(sample), .rx_s(samp_val), .rx_q1(q1), .rx_q1_s(vp[rxpin]),
        .rx_valid(cd_rxv), .rx_bit(cd_rxb), .rx_err(cd_rxerr)
    );

    // rule k registers sit at a stride of 3 bank words from rule 0
    localparam int WM = int'(tse_regs_pkg::LW_RULE0_MATCH), WR = int'(tse_regs_pkg::LW_RULE0_RESP_POS);
    localparam int WC = int'(tse_regs_pkg::LW_RULE0_CTL), RS = int'(tse_regs_pkg::LW_RULE1_MATCH) - WM;
    logic [K*PW-1:0] r_pat, r_msk;
    logic [K*9-1:0]  r_pos;
    logic [K*16-1:0] r_resp;
    logic [K*14-1:0] r_ctl;
    always_comb
        for (int k = 0; k < K; k++) begin
            r_pat[PW*k +: PW]  = cw[WM + RS*k][tse_regs_pkg::LR_RULE0_MATCH_PATTERN_LO +: PW];
            r_msk[PW*k +: PW]  = cw[WM + RS*k][tse_regs_pkg::LR_RULE0_MATCH_MASK_LO +: PW];
            r_pos[9*k +: 9]    = cw[WR + RS*k][tse_regs_pkg::LR_RULE0_RESP_POS_ANY_HI:tse_regs_pkg::LR_RULE0_RESP_POS_POS_LO];
            r_resp[16*k +: 16] = cw[WR + RS*k][tse_regs_pkg::LR_RULE0_RESP_POS_RESP_HI:tse_regs_pkg::LR_RULE0_RESP_POS_RESP_LO];
            r_ctl[14*k +: 14]  = cw[WC + RS*k][tse_regs_pkg::LR_RULE1_CTL_CHAIN_HI:tse_regs_pkg::LR_RULE0_CTL_RLEN_M1_LO];
        end

    logic drv_en, drv_val, hit_p, abort_p;
    logic [K-1:0] hits;
    tse_resp_rule #(.K(K), .PW(PW)) u_rule (
        .clk, .rst_n, .cfg_pat(r_pat), .cfg_msk(r_msk), .cfg_pos(r_pos), .cfg_resp(r_resp),
        .cfg_ctl(r_ctl), .cfg_src(rclk[tse_regs_pkg::LR_RULE_CLK_SRC_HI]),
        .cfg_edge(rclk[tse_regs_pkg::LR_RULE_CLK_EDGE_HI]),
        .cfg_tdly(rclk[tse_regs_pkg::LR_RULE_CLK_TICK_DELAY_HI:tse_regs_pkg::LR_RULE_CLK_TICK_DELAY_LO]),
        .cfg_hold(rclk[tse_regs_pkg::LR_RULE_CLK_HOLD_HI]), .seq_wr(pwr[txpin] || !run), .frame_rst,
        .bit_valid(rx_v), .bit_in(rx_b), .bit_start,
        .pin_clk(vp[rclk[tse_regs_pkg::LR_RULE_CLK_CLK_PIN_HI:tse_regs_pkg::LR_RULE_CLK_CLK_PIN_LO]]),
        .pin_dat(vp[rxpin]), .crc_ok,
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
    assign st_din    = crc_ph ? crc_txb : (tx_msb ? txsr[31] : txsr[0]);
    assign crc_load  = load_pend;
    assign crc_shift = st_take && crc_ph;
    assign arb_lost  = arb_en && tx_active && sample && line && !samp_val;
    assign abort_any = tx_abort_c || arb_lost || abort_p;
    assign cd_step   = st_step || (resp_coded && drv_en && bit_start);
    assign cd_bit    = (resp_coded && drv_en) ? drv_val : st_dout;
    assign tx_fin    = tx_step && !data_left && !st_pend;
    assign line_set  = !run || (cmd_v && cmd[tse_regs_pkg::LR_CMD_LINE_SET_HI]) || arb_lost || tx_fin;   // stopped: idle level
    assign line_val  = (!run || arb_lost || tx_fin) ? idle : cmd[tse_regs_pkg::LR_CMD_LINE_VAL_HI];
    assign tx_feed   = st_take && tcnt != 6'd0;
    assign crc_bv    = (tx_crc && tx_feed) || (rx_crc && !tx_crc && rx_v);
    assign crc_bin   = tx_crc ? st_din : rx_b;

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            txsr <= '0; tcnt <= '0; ccnt <= '0; app_crc <= 1'b0; load_pend <= 1'b0; tx_active <= 1'b0;
        end else if (abort_any) begin
            tcnt <= '0; ccnt <= '0; load_pend <= 1'b0; tx_active <= 1'b0;
        end else if (we && wa == tse_regs_pkg::LR_TXDATA) begin
            txsr <= wd; tcnt <= {1'b0, txctl[4:0]} + 6'd1; app_crc <= txctl[5]; tx_active <= 1'b1;
        end else begin
            load_pend <= 1'b0;
            if (load_pend) ccnt <= {1'b0, crc_wm1} + 6'd1;
            if (tx_feed) begin
                txsr <= tx_msb ? {txsr[30:0], 1'b0} : {1'b0, txsr[31:1]};
                tcnt <= tcnt - 6'd1;
                if (tcnt == 6'd1 && app_crc) load_pend <= 1'b1;
            end
            if (crc_shift) ccnt <= ccnt - 6'd1;
            if (tx_fin) tx_active <= 1'b0;
        end

    // A frame's half-bit strobes count only from its first bit: a TXDATA
    // write between a bit start and its half strobe would otherwise drive the
    // previous frame's last bit (the codec's mbit) at that strobe.
    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) tx_begun <= 1'b0;
        else        tx_begun <= tx_active && (tx_begun || tx_step);

    // RX word assembly and sticky flags
    logic [31:0] rxsr;
    logic [4:0]  rcnt;
    logic        rx_full, serr_s, abort_s, rd_rx;
    assign rd_rx = (h_re && h_addr == tse_regs_pkg::LR_RXDATA) || (s_re && s_addr == tse_regs_pkg::LR_RXDATA);

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            rxsr <= '0; rcnt <= '0; rx_full <= 1'b0; serr_s <= 1'b0; abort_s <= 1'b0;
            txctl <= '0; dpll_en <= 1'b0;
        end else begin
            if (we && wa == tse_regs_pkg::LR_TXCTL) txctl <= wd[5:0];
            if (cmd_v && cmd[tse_regs_pkg::LR_CMD_DPLL_ON_HI])  dpll_en <= 1'b1;
            if (cmd_v && cmd[tse_regs_pkg::LR_CMD_DPLL_OFF_HI]) dpll_en <= 1'b0;
            if (rd_rx) rx_full <= 1'b0;
            if (frame_rst) begin
                rcnt <= '0; serr_s <= 1'b0; abort_s <= 1'b0;
            end else begin
                if (rx_v) begin
                    rxsr <= rx_msb ? {rxsr[30:0], rx_b} : {rx_b, rxsr[31:1]};
                    if (rcnt == rx_len_m1) begin
                        rcnt <= '0; rx_full <= 1'b1;
                    end else rcnt <= rcnt + 5'd1;
                end
                if (rx_serr || cd_rxerr) serr_s <= 1'b1;
                if (arb_lost || abort_p) abort_s <= 1'b1;
            end
        end

    // timer: restarts on frame reset; its flag replaces event 0 when ev0_sel is set
    logic        tmr_exp, tmr_flag, tmr_tick, ev0_sel, tmr_ack;
    logic [15:0] tmr_count;
    assign ev0_sel = tctl[tse_regs_pkg::LR_TIMER_CTL_EV0_SEL_HI];
    tse_timer u_tmr (
        .clk, .rst_n, .cfg_inc(cw[tse_regs_pkg::LW_TIMER_INC][23:0]),
        .cfg_reload(tctl[tse_regs_pkg::LR_TIMER_CTL_RELOAD_HI:tse_regs_pkg::LR_TIMER_CTL_RELOAD_LO]),
        .cfg_on_tick(tctl[tse_regs_pkg::LR_TIMER_CTL_ON_TICK_HI]),
        .cfg_auto(1'b0), .en(tctl[tse_regs_pkg::LR_TIMER_CTL_EN_HI]), .start(frame_rst), .tick(tmr_tick),
        .expired(tmr_exp), .count(tmr_count)
    );
    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n)         tmr_flag <= 1'b0;
        else if (frame_rst || tmr_ack) tmr_flag <= 1'b0;
        else if (tmr_exp)   tmr_flag <= 1'b1;

    // event map order: ABORT, TXIDLE, BITSTART, ERR, CRCOK, RULEHIT, RXFULL, TXNEED or TIMER
    assign ev = {abort_s, !tx_active, bit_start, serr_s, crc_ok, |hits, rx_full,
                 ev0_sel ? tmr_flag : !data_left};

    logic [31:0] status;
    logic s_aflag, s_tflag;
    logic [15:0] clock;
    logic [7:0]  s_pc;
    always_comb begin
        status = '0;
        status[tse_regs_pkg::LR_STATUS_EV_HI:tse_regs_pkg::LR_STATUS_EV_LO]             = ev;
        status[tse_regs_pkg::LR_STATUS_TFLAG_LO]                   = s_tflag;
        status[tse_regs_pkg::LR_STATUS_ILLEGAL_LO]                 = halted;
        status[tse_regs_pkg::LR_STATUS_RUNNING_LO]                 = run && !halted;
        status[tse_regs_pkg::LR_STATUS_HITS_LO +: K]               = hits;
        status[tse_regs_pkg::LR_STATUS_RX_COUNT_HI:tse_regs_pkg::LR_STATUS_RX_COUNT_LO] = rcnt;
        status[tse_regs_pkg::LR_STATUS_TX_ACTIVE_LO]               = tx_active;
        status[tse_regs_pkg::LR_STATUS_CRC_BUSY_LO]                = crc_busy;
        status[tse_regs_pkg::LR_STATUS_SERR_LO]                    = serr_s;
        status[tse_regs_pkg::LR_STATUS_ABORT_LO]                   = abort_s;
        status[tse_regs_pkg::LR_STATUS_AFLAG_LO]                   = s_aflag;
        status[tse_regs_pkg::LR_STATUS_TIMER_LO]                   = tmr_flag;
        status[tse_regs_pkg::LR_STATUS_PC_HI:tse_regs_pkg::LR_STATUS_PC_LO]             = s_pc;
    end
    always_comb begin
        case (s_addr)
            tse_regs_pkg::LR_RXDATA:     rdata = rxsr;
            tse_regs_pkg::LR_STATUS:     rdata = status;
            tse_regs_pkg::LR_CRC_RESULT: rdata = crc_res;
            tse_regs_pkg::LR_CLOCK:      rdata = {16'd0, clock};
            tse_regs_pkg::LR_TS:         rdata = ts;
            default:                     rdata = 32'd0;
        endcase
        case (h_addr)
            tse_regs_pkg::LR_RXDATA:     h_rdata = rxsr;
            tse_regs_pkg::LR_STATUS:     h_rdata = status;
            tse_regs_pkg::LR_CRC_RESULT: h_rdata = crc_res;
            tse_regs_pkg::LR_CLOCK:      h_rdata = {16'd0, clock};
            tse_regs_pkg::LR_TS:         h_rdata = ts;
            default:                     h_rdata = 32'd0;
        endcase
    end

    tse_seq #(.LATCH(LATCH)) u_seq (
        .clk, .rst_n, .run, .imem_req, .imem_addr, .imem_gnt, .imem_rdata,
        .start_pc(cw[tse_regs_pkg::LW_LANE_PROG][tse_regs_pkg::LR_LANE_PROG_START_PC_HI:tse_regs_pkg::LR_LANE_PROG_START_PC_LO]),
        .dmem_req, .dmem_we, .dmem_addr, .dmem_wdata, .dmem_gnt, .dmem_rdata,
        .alt_cfg, .flg({1'b1, synced, !h_flags[1], h_flags[0], edge_seen, half, sample, 1'b0}),
        .pin_in(vp), .pin_out(pins), .pin_oe(dirs), .pin_wr(pwr),
        .side_en, .ss_base, .jmp_pin, .ev, .chip_flags,
        .gt({cw[tse_regs_pkg::LW_GUARD23], cw[tse_regs_pkg::LW_GUARD01]}), .clock, .pc_o(s_pc),
        .csr_we(s_we), .csr_re(s_re), .csr_addr(s_addr), .csr_wdata(s_wdata), .csr_rdata(rdata),
        .cmd_we(s_cmd_we), .cmd(s_cmd),
        .h_tx_we, .h_tx_data, .h_tx_full(h_flags[0]), .h_rx_re, .h_rx_data, .h_rx_full(h_flags[1]),
        .irq, .sig_we, .sig_mask, .sig_val, .emit_v(ev_valid), .emit_code(ev_code), .emit_data(ev_data),
        .timer_ack(tmr_ack), .halted, .aflag_o(s_aflag), .tflag_o(s_tflag)
    );

    logic [3:0] o4, e4;
    always_comb begin
        o4 = pins;
        e4 = dirs;
        if (tx_owns_pin) begin
            o4[txpin] = line; e4[txpin] = 1'b1;
        end
        if (drv_en && !resp_coded) begin
            o4[txpin] = drv_val; e4[txpin] = 1'b1;
        end
    end
    assign vout = V'(o4);
    assign voe  = V'(e4);
    wire _unused = &{edge_seen, hit_p, tmr_tick, tmr_count, s_tflag, lcfg[31:28], 1'b0};
endmodule
`default_nettype wire
