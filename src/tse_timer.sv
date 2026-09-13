`default_nettype none
// Lane timer: an NCO tick generator plus a 16-bit down counter that counts
// either NCO ticks or clock cycles. expired pulses when the count reaches
// zero; auto-reload makes it periodic. inc and reload come from config.
module tse_timer (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [23:0] cfg_inc,       // tick rate = f_clk * inc / 2^24
    input  logic [15:0] cfg_reload,
    input  logic        cfg_on_tick,   // 1: count NCO ticks, 0: count cycles
    input  logic        cfg_auto,      // reload automatically at zero
    input  logic        en,
    input  logic        start,         // load the counter now
    output logic        tick,
    output logic        expired,
    output logic [15:0] count
);
    tse_nco #(.W(24)) u_nco (.clk, .rst_n, .en, .inc(cfg_inc), .tick);

    logic step;
    assign step = en && (cfg_on_tick ? tick : 1'b1);

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            count <= '0; expired <= 1'b0;
        end else begin
            expired <= 1'b0;
            if (start)
                count <= cfg_reload;
            else if (step && count != 16'd0) begin
                count <= count - 16'd1;
                if (count == 16'd1) begin
                    expired <= 1'b1;
                    if (cfg_auto) count <= cfg_reload;
                end
            end
        end
endmodule
`default_nettype wire
