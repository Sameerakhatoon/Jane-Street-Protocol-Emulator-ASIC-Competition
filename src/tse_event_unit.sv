`default_nettype none
// Event unit: chip-wide timestamp, event log, sequence trigger, chip flags.
// A lane event (ev_valid with a 6-bit code and 8 data bits) is captured with
// timestamp bits [16:0] of its own cycle into the lane's holding register
// when logging is on for that lane. One record per cycle enters the FIFO: a
// pending TRIG record first, then the holding registers in rotating order.
// Record: {lane[31:30], code[29:24], data[23:16], ts[15:0]}; code 0x3E is a
// TRIG record, 0x3F a TIME record whose low half is timestamp bits [31:16].
// A TIME record goes in before the first record of every new 65,536-cycle
// epoch, so the host rebuilds full timestamps. An event that finds its
// holding register still full is dropped and counted (saturating).
// Trigger: stage s matches an event when its lane is in the lane mask and
// code and data agree under their masks; a nonzero window bounds the cycles
// since the previous stage matched, and an expired window returns the
// pointer to 0. Matching the last stage fires: TRIG flag, a pulse of
// width_m1 + 1 cycles, a TRIG record, a stop mask for RUN, then re-arm or stay
// fired until arm. The trigger sees every event, logged or not.
// chip_flags = {TICK, TRIG, CF3..CF0}; on a same-cycle CF write the highest
// lane wins.
module tse_event_unit #(
    parameter int LANES = 3,
    parameter int DEPTH = 4,
    localparam int DW = $clog2(DEPTH)
) (
    input  logic               clk,
    input  logic               rst_n,
    input  logic [9:0]         cfg_ctl,       // {tick_k[9:5], lane_mask[4:1], log_en[0]}
    input  logic [4*32-1:0]    cfg_match,     // per stage {data_mask, data, code_mask, code, lanes}
    input  logic [4*16-1:0]    cfg_win,       // per stage window in cycles, 0 = no limit
    input  logic [16:0]        cfg_trig,      // {stop_mask, pol, width_m1, rearm, en, last}
    input  logic [LANES-1:0]   ev_valid,
    input  logic [LANES*6-1:0] ev_code,
    input  logic [LANES*8-1:0] ev_data,
    input  logic [LANES-1:0]   cf_we,
    input  logic [LANES*4-1:0] cf_mask,
    input  logic [LANES*4-1:0] cf_val,
    input  logic               pop,
    input  logic               clr_fifo,
    input  logic               clr_drop,
    input  logic               arm,
    output logic [31:0]        head,
    output logic [15:0]        status,        // {stage, fired, armed, dropped, count}
    output logic [31:0]        ts,
    output logic [5:0]         chip_flags,
    output logic               trig_level,
    output logic [3:0]         trig_stop      // one-cycle pulse: lanes whose RUN bit clears
);
    localparam logic [5:0] CODE_TRIG = 6'h3E, CODE_TIME = 6'h3F;

    logic       log_en;
    logic [3:0] lmask;
    logic [4:0] tick_k;
    assign log_en = cfg_ctl[0];
    assign lmask  = cfg_ctl[4:1];
    assign tick_k = cfg_ctl[9:5];

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) ts <= '0;
        else        ts <= ts + 32'd1;

    // ---------------- log
    logic [LANES-1:0] hold;
    logic [30:0]      hw [LANES];           // {code, data, ts[16:0]}
    logic             tp;                   // TRIG record pending
    logic [26:0]      tw;                   // {lane, data, ts[16:0]}
    logic [15:0]      last_ep;
    logic             ep_ok;
    logic [31:0]      fifo [DEPTH];
    logic [DW:0]      wp, rp, cnt;
    logic [7:0]       dropped;
    logic [1:0]       rr;

    logic             any, sel_t, full, empty, wr, need_time, done;
    logic [1:0]       sel_l, c_lane;
    logic [5:0]       c_code;
    logic [7:0]       c_data;
    logic [16:0]      c_ts;
    logic [15:0]      c_ep;
    logic [31:0]      wword;
    logic [LANES-1:0] take_l;

    assign cnt   = wp - rp;
    assign full  = cnt[DW];
    assign empty = cnt == '0;
    assign head  = fifo[rp[DW-1:0]];

    always_comb begin
        int j;
        j = 0;
        sel_t = tp; any = tp; sel_l = '0;
        c_lane = tw[26:25]; c_code = CODE_TRIG; c_data = tw[24:17]; c_ts = tw[16:0];
        if (!tp)
            for (int k = LANES - 1; k >= 0; k--) begin
                j = int'(rr) + k;
                if (j >= LANES) j = j - LANES;
                if (hold[j]) begin
                    any = 1'b1; sel_l = 2'(j); c_lane = 2'(j);
                    c_code = hw[j][30:25]; c_data = hw[j][24:17]; c_ts = hw[j][16:0];
                end
            end
        c_ep      = (c_ts[16] == ts[16]) ? ts[31:16] : ts[31:16] - 16'd1;
        need_time = !ep_ok || c_ep != last_ep;
        wr        = any && !full;
        done      = wr && !need_time;
        wword     = need_time ? {2'd0, CODE_TIME, 8'd0, c_ep} : {c_lane, c_code, c_data, c_ts[15:0]};
        for (int i = 0; i < LANES; i++) take_l[i] = done && !sel_t && sel_l == 2'(i);
    end

    // ---------------- trigger
    logic [1:0]       st;
    logic             fired, tflag, hit, expired, fire;
    logic [15:0]      wcnt, win;
    logic [8:0]       pcnt;
    logic [31:0]      m;
    logic [LANES-1:0] hl;
    logic [1:0]       f_lane;
    logic             t_en, t_rearm, t_pol;
    logic [1:0]       t_last;
    assign t_last  = cfg_trig[1:0];
    assign t_en    = cfg_trig[2];
    assign t_rearm = cfg_trig[3];
    assign t_pol   = cfg_trig[12];

    always_comb begin
        m   = cfg_match[32*st +: 32];
        win = cfg_win[16*st +: 16];
        f_lane = '0;
        for (int i = LANES - 1; i >= 0; i--) begin
            hl[i] = ev_valid[i] && m[i] && ((ev_code[6*i +: 6] ^ m[9:4]) & m[15:10]) == 6'd0
                 && ((ev_data[8*i +: 8] ^ m[23:16]) & m[31:24]) == 8'd0;
            if (hl[i]) f_lane = 2'(i);
        end
        hit     = |hl && t_en && !fired;
        expired = st != 2'd0 && win != 16'd0 && wcnt > win;
        fire    = hit && !expired && st == t_last;
    end

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            hold <= '0; tp <= 1'b0; tw <= '0; last_ep <= '0; ep_ok <= 1'b0;
            wp <= '0; rp <= '0; dropped <= '0; rr <= '0;
            for (int i = 0; i < LANES; i++) hw[i] <= '0;
            for (int i = 0; i < DEPTH; i++) fifo[i] <= '0;
        end else begin
            for (int i = 0; i < LANES; i++) begin
                if (take_l[i]) hold[i] <= 1'b0;
                if (ev_valid[i] && log_en && lmask[i]) begin
                    if (hold[i] && !take_l[i]) begin
                        if (dropped != 8'hFF) dropped <= dropped + 8'd1;
                    end else begin
                        hold[i] <= 1'b1;
                        hw[i]   <= {ev_code[6*i +: 6], ev_data[8*i +: 8], ts[16:0]};
                    end
                end
            end
            if (done && sel_t) tp <= 1'b0;
            if (fire && log_en) begin
                if (tp && !(done && sel_t)) begin
                    if (dropped != 8'hFF) dropped <= dropped + 8'd1;
                end else begin
                    tp <= 1'b1;
                    tw <= {f_lane, {4'(hl), 2'd0, st}, ts[16:0]};
                end
            end
            if (done && !sel_t) rr <= (32'(sel_l) + 1 >= LANES) ? 2'd0 : sel_l + 2'd1;
            if (wr) begin
                fifo[wp[DW-1:0]] <= wword;
                wp <= wp + 1'b1;
                if (need_time) begin last_ep <= c_ep; ep_ok <= 1'b1; end
            end
            if (clr_fifo)            rp <= wp;
            else if (pop && !empty)  rp <= rp + 1'b1;
            if (clr_drop) dropped <= '0;
        end

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            st <= '0; fired <= 1'b0; tflag <= 1'b0; wcnt <= '0; pcnt <= '0; trig_stop <= '0;
        end else begin
            trig_stop <= '0;
            if (pcnt != 9'd0)     pcnt <= pcnt - 9'd1;
            if (wcnt != 16'hFFFF) wcnt <= wcnt + 16'd1;
            if (arm) begin
                st <= '0; fired <= 1'b0; tflag <= 1'b0;
            end else if (!t_en || expired) begin
                st <= '0;
            end else if (fire) begin
                tflag     <= 1'b1;
                pcnt      <= {1'b0, cfg_trig[11:4]} + 9'd1;
                trig_stop <= cfg_trig[16:13];
                st        <= '0;
                fired     <= !t_rearm;
            end else if (hit) begin
                st   <= st + 2'd1;
                wcnt <= 16'd1;
            end
        end
    assign trig_level = t_pol ? pcnt != 9'd0 : pcnt == 9'd0;

    // ---------------- chip flags
    logic [3:0]  cf;
    logic [31:0] kmask;
    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) cf <= '0;
        else begin
            logic [3:0] c;
            c = cf;
            for (int i = 0; i < LANES; i++)
                if (cf_we[i]) c = (c & ~cf_mask[4*i +: 4]) | (cf_val[4*i +: 4] & cf_mask[4*i +: 4]);
            cf <= c;
        end
    assign kmask      = (32'd1 << tick_k) - 32'd1;
    assign chip_flags = {(ts & kmask) == 32'd0, tflag, cf};
    assign status     = {st, fired, t_en && !fired, dropped, 4'(cnt)};
endmodule
`default_nettype wire
