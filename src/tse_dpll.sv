`default_nettype none
// Edge-tracking bit timer (config as inputs). Phase in 1/16 cycle,
// 20 bits. s_p: pin sampled at the last rising edge; s_n: same pin sampled at
// the falling edge half a cycle earlier. Hard sync on an armed edge, otherwise
// resync limited to cfg_sjw, split over two cycles for timing.
// The transmit strobes (bit_start, half) fire in the cycle whose next phase
// s reaches their point: the codec acts on them at the next edge, so the
// line changes exactly there and a Manchester cell splits evenly. The
// receive strobes (q1, sample) fire in the first cycle whose phase is at or
// past their point, because they take the pin value of that same cycle.
module tse_dpll (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [19:0] cfg_period,
    input  logic [19:0] cfg_spt,       // sample point
    input  logic [15:0] cfg_sjw,
    input  logic        cfg_resync,
    input  logic        cfg_dual,
    input  logic        cfg_rise,
    input  logic        cfg_fall,
    input  logic        enable,
    input  logic        hard_arm,
    input  logic        s_p,
    input  logic        s_n,
    output logic        bit_start,
    output logic        half,
    output logic        q1,
    output logic        sample,
    output logic        samp_val,
    output logic        edge_seen,
    output logic        synced         // not waiting for a hard sync
);
    logic [19:0] ph, half_p, q_p, elapsed, lag, sj, e, mag;
    logic [20:0] e_raw, s, delta, delta_q;
    logic        prev_p, armed, edge_q, hard, wrap, q_done, h_done, s_done;

    assign half_p  = {1'b0, cfg_period[19:1]};
    assign q_p     = {2'b0, cfg_period[19:2]};
    assign sj      = {4'd0, cfg_sjw};
    assign lag     = !cfg_dual ? 20'd8 : (s_n != prev_p) ? 20'd12 : 20'd4;
    assign elapsed = lag + 20'd16;
    assign edge_q  = enable && ((!prev_p && s_p && cfg_rise) || (prev_p && !s_p && cfg_fall));
    assign synced  = !armed;
    assign hard    = edge_q && armed;
    assign e_raw   = {1'b0, ph} - {1'b0, lag};             // our phase when the edge happened
    assign e       = e_raw[20] ? e_raw[19:0] + cfg_period : e_raw[19:0];

    always_comb begin                                      // cycle 1: bounded correction
        if (e < half_p) begin
            mag   = (e > sj) ? sj : e;
            delta = -{1'b0, mag};
        end else begin
            mag   = ((cfg_period - e) > sj) ? sj : cfg_period - e;
            delta = {1'b0, mag};
        end
    end
    assign s    = {1'b0, ph} + 21'd16 + delta_q;            // cycle 2: one three-input add
    assign wrap = !s[20] && s[19:0] >= cfg_period;

    assign edge_seen = edge_q;
    assign bit_start = enable && (wrap || hard);
    assign q1        = enable && !armed && !q_done && ph >= q_p;   // each strobe once per bit
    assign half      = enable && !h_done && !s[20] && s[19:0] >= half_p;
    assign sample    = enable && !armed && !s_done && ph >= cfg_spt;
    assign samp_val  = s_p;

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            ph <= '0; prev_p <= 1'b0; armed <= 1'b0; delta_q <= '0;
            q_done <= 1'b0; h_done <= 1'b0; s_done <= 1'b0;
        end else begin
            prev_p  <= s_p;
            delta_q <= (edge_q && cfg_resync && !armed) ? delta : 21'd0;
            if (hard_arm) armed <= 1'b1;
            if (hard)     armed <= 1'b0;
            if (!enable)    ph <= '0;
            else if (hard)  ph <= elapsed;
            else if (s[20]) ph <= '0;
            else if (wrap)  ph <= s[19:0] - cfg_period;
            else            ph <= s[19:0];
            q_done <= enable && !bit_start && (q_done || q1);    // a restart begins a fresh bit
            h_done <= enable && !bit_start && (h_done || half);
            s_done <= enable && !bit_start && (s_done || sample);
        end
endmodule
`default_nettype wire
