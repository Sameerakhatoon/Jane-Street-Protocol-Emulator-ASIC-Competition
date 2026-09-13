`default_nettype none
// Event unit: chip-wide timestamp, event log FIFO, sequence trigger.
// Lanes raise ev_valid with a 4-bit code. Each event is captured with the
// timestamp of its own cycle into a per-lane holding register, then written
// to the FIFO (one per cycle, lowest lane first) as {lane[1:0], code[3:0],
// ts[25:0]}. A lost event (holding register or FIFO full) sets overflow.
// The trigger walks STAGES stages; stage s matches an event when its lane
// is in lane_mask and (code ^ want) & code_mask == 0. A nonzero window
// bounds the cycles from the previous stage to this one; on timeout the
// sequence restarts. Matching the last enabled stage pulses trig_out for
// trig_len cycles and records trig_ts.
// Stage word: [31] en, [27:24] lane_mask, [23:20] code_mask, [19:16] want,
// [15:0] window. Control word: [7:0] trig_len.
module tse_event_unit #(
    parameter int LANES  = 3,
    parameter int DEPTH  = 8,
    parameter int STAGES = 4,
    localparam int DW = $clog2(DEPTH),
    localparam int SB = $clog2(STAGES)
) (
    input  logic                 clk,
    input  logic                 rst_n,
    input  logic                 run,
    input  logic [STAGES*32-1:0] cfg_stage,
    input  logic [7:0]           cfg_trig_len,
    input  logic [LANES-1:0]     ev_valid,
    input  logic [LANES*4-1:0]   ev_code,
    input  logic                 pop,
    output logic [31:0]          head,
    output logic                 empty,
    output logic                 overflow,
    output logic [31:0]          ts,
    output logic [31:0]          trig_ts,
    output logic                 trig_out
);
    // timestamp
    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n)    ts <= '0;
        else if (run)  ts <= ts + 32'd1;

    // per-lane capture, then one FIFO write per cycle
    logic [LANES-1:0] hold;
    logic [31:0]      hold_w [LANES];
    logic [31:0]      fifo [DEPTH];
    logic [DW:0]      wp, rp;
    logic             full, wr;
    logic [31:0]      wword;
    logic [LANES-1:0] take;

    assign full  = (wp[DW] != rp[DW]) && (wp[DW-1:0] == rp[DW-1:0]);
    assign empty = wp == rp;
    assign head  = fifo[rp[DW-1:0]];

    always_comb begin
        take = '0; wr = 1'b0; wword = '0;
        for (int i = LANES - 1; i >= 0; i--)
            if (hold[i]) begin take = '0; take[i] = 1'b1; wr = 1'b1; wword = hold_w[i]; end
    end

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            hold <= '0; wp <= '0; rp <= '0; overflow <= 1'b0;
            for (int i = 0; i < LANES; i++) hold_w[i] <= '0;
            for (int i = 0; i < DEPTH; i++) fifo[i] <= '0;
        end else begin
            for (int i = 0; i < LANES; i++) begin
                if (take[i] && !full) hold[i] <= 1'b0;
                if (ev_valid[i]) begin
                    if (hold[i] && !(take[i] && !full)) overflow <= 1'b1;
                    else begin
                        hold[i]   <= 1'b1;
                        hold_w[i] <= {2'(i), ev_code[4*i +: 4], ts[25:0]};
                    end
                end
            end
            if (wr && !full) begin
                fifo[wp[DW-1:0]] <= wword; wp <= wp + 1'b1;
            end
            if (pop && !empty) rp <= rp + 1'b1;
        end

    // sequence trigger
    logic [SB-1:0] st;
    logic [15:0]   win;
    logic [7:0]    tcnt;
    logic [31:0]   sw, nw;
    logic          hit, last;
    logic [(STAGES+1)*32-1:0] pad;
    assign pad = {32'd0, cfg_stage};                 // stage STAGES reads as disabled
    wire _unused = &{nw[30:16], 1'b0};

    always_comb begin
        sw  = pad[32*st +: 32];
        nw  = pad[32*(32'(st) + 1) +: 32];
        hit = 1'b0;
        for (int i = 0; i < LANES; i++)
            if (ev_valid[i] && sw[24 + i] && ((ev_code[4*i +: 4] ^ sw[19:16]) & sw[23:20]) == 4'd0)
                hit = 1'b1;
        hit  = hit && sw[31];
        last = !nw[31];
    end

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            st <= '0; win <= '0; tcnt <= '0; trig_ts <= '0; trig_out <= 1'b0;
        end else begin
            trig_out <= tcnt != 8'd0;
            if (tcnt != 8'd0) tcnt <= tcnt - 8'd1;
            if (!run) begin
                st <= '0;
            end else if (hit && last) begin
                st <= '0; tcnt <= cfg_trig_len; trig_ts <= ts;
            end else if (hit) begin
                st  <= st + 1'b1;
                win <= nw[15:0];
            end else if (st != '0 && win != 16'd0) begin
                win <= win - 16'd1;
                if (win == 16'd1) st <= '0;                      // window missed: restart
            end
        end
endmodule
`default_nettype wire
