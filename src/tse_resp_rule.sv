`default_nettype none
// K response rules (config as inputs). Rule k matches the last PW counted bits
// against (pattern, mask) when the 8-bit frame bit counter equals pos (or
// any is set), optionally gated by a condition, then drives up to 16
// response bits and/or raises abort.
// pos = {any[8], pos[7:0]}; ctl = {en, abort, drive, cond[1:0], offset[3:0], rlen[3:0]};
// lowest k wins. The counter saturates at 255 (the compiler rejects
// fixed-position rules with pos + offset > 254).
// RULE_CLK (shared by the K rules): cfg_src 0 = bit mode: recovered bits
// (bit_valid, bit_in) and bit_start pacing; offset 0 drives inside the
// matching bit. cfg_src 1 = pin-edge mode: each counted edge of pin_clk
// (cfg_edge 0 rising, 1 falling) counts a bit, shifts in pin_dat as it was
// the cycle before, and paces the response; offset counts counted edges after
// the match (0 = the matching edge); every drive change happens cfg_tdly
// cycles after its edge (the drive register then changes at the end of that
// cycle).
module tse_resp_rule #(
    parameter int K  = 2,
    parameter int PW = 16
) (
    input  logic          clk,
    input  logic          rst_n,
    input  logic [K*PW-1:0] cfg_pat,
    input  logic [K*PW-1:0] cfg_msk,
    input  logic [K*9-1:0]  cfg_pos,
    input  logic [K*16-1:0] cfg_resp,
    input  logic [K*13-1:0] cfg_ctl,
    input  logic          cfg_src,
    input  logic          cfg_edge,
    input  logic [7:0]    cfg_tdly,
    input  logic          frame_rst,
    input  logic          bit_valid,
    input  logic          bit_in,
    input  logic          bit_start,
    input  logic          pin_clk,
    input  logic          pin_dat,
    input  logic          crc_ok,
    input  logic          ext_flag,
    input  logic          flags_clr,
    output logic          drv_en,
    output logic          drv_val,
    output logic [K-1:0]  hit_flags,
    output logic          hit_pulse,
    output logic          abort_pulse
);
    logic clk_q, dat_q, cedge, bv, bi, bs, delayed;
    assign cedge   = cfg_edge ? (!pin_clk && clk_q) : (pin_clk && !clk_q);
    assign bv      = cfg_src ? cedge : bit_valid;
    assign bi      = cfg_src ? dat_q : bit_in;
    assign delayed = cfg_src && cfg_tdly != 8'd0;

    logic [7:0] tk;                                    // tick delay after a counted edge
    assign bs = !cfg_src ? bit_start : delayed ? tk == 8'd1 : cedge;

    logic [PW-1:0] hist, hist_n;
    logic [7:0]    cnt;
    logic [K-1:0]  match;
    assign hist_n = {hist[PW-2:0], bi};

    always_comb
        for (int k = 0; k < K; k++) begin
            logic cond_ok;
            case (cfg_ctl[13*k+8 +: 2])
                2'd1:    cond_ok = crc_ok;
                2'd2:    cond_ok = !crc_ok;
                2'd3:    cond_ok = ext_flag;
                default: cond_ok = 1'b1;
            endcase
            match[k] = cfg_ctl[13*k+12] && bv && cond_ok
                            && (((hist_n ^ cfg_pat[PW*k +: PW]) & cfg_msk[PW*k +: PW]) == '0)
                            && (cfg_pos[9*k+8] || cfg_pos[9*k +: 8] == cnt);
        end

    logic [12:0] wctl;
    logic [15:0] wresp;
    logic        any;
    always_comb begin                                  // lowest-numbered match wins
        wctl = '0; wresp = '0; any = 1'b0;
        for (int k = K - 1; k >= 0; k--)
            if (match[k]) begin
                wctl = cfg_ctl[13*k +: 13]; wresp = cfg_resp[16*k +: 16]; any = 1'b1;
            end
    end

    // in pin-edge mode with a tick delay, the matching edge itself is the first
    // pacing event, so the response waits for offset further ones
    logic        now;
    logic [4:0]  wait0;
    assign now   = wctl[7:4] == 4'd0 && !delayed;
    assign wait0 = {1'b0, wctl[7:4]} + {4'd0, delayed};

    logic        pending;
    logic [4:0]  wait_n;
    logic [3:0]  left;
    logic [15:0] rsr;
    assign drv_val = rsr[0];
    wire _unused = &{hist[PW-1], wctl[12], wctl[9:8], 1'b0};

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            clk_q <= 1'b0; dat_q <= 1'b0; tk <= '0;
            hist <= '0; cnt <= '0; hit_flags <= '0; hit_pulse <= 1'b0; abort_pulse <= 1'b0;
            drv_en <= 1'b0; pending <= 1'b0; wait_n <= '0; left <= '0; rsr <= '0;
        end else begin
            clk_q       <= pin_clk;
            dat_q       <= pin_dat;
            hit_pulse   <= any;
            abort_pulse <= any && wctl[11];
            if (delayed && cedge) tk <= cfg_tdly;
            else if (tk != 8'd0)  tk <= tk - 8'd1;
            if (frame_rst) begin
                hist <= '0; cnt <= '0;
            end else if (bv) begin
                hist <= hist_n;
                if (cnt != 8'hff) cnt <= cnt + 8'd1;
            end
            if (flags_clr) hit_flags <= '0;
            else           hit_flags <= hit_flags | match;
            if (any && wctl[10]) begin
                rsr <= wresp; left <= wctl[3:0];
                if (now) begin
                    drv_en <= 1'b1; pending <= 1'b0;
                end else begin
                    pending <= 1'b1; wait_n <= wait0;
                end
            end else if (bs) begin
                if (pending) begin
                    wait_n <= wait_n - 5'd1;
                    if (wait_n == 5'd1) begin
                        pending <= 1'b0; drv_en <= 1'b1;
                    end
                end else if (drv_en) begin
                    if (left == 4'd0) drv_en <= 1'b0;
                    else begin
                        rsr <= {1'b0, rsr[15:1]}; left <= left - 4'd1;
                    end
                end
            end
        end
endmodule
`default_nettype wire
