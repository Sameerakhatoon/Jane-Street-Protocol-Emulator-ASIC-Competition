`default_nettype none
// Bit stuffer (TX) and destuffer (RX), config as inputs.
// After cfg_n equal bits of a selected polarity, insert (TX) or drop and
// check (RX) one complement bit. cfg_mode[0]: runs of ones, [1]: runs of
// zeros (3 = CAN, 1 = USB).
module tse_stuffer (
    input  logic       clk,
    input  logic       rst_n,
    input  logic [3:0] cfg_n,
    input  logic [1:0] cfg_mode,
    input  logic       clear,
    input  logic       tx_step,
    input  logic       tx_din,
    output logic       tx_take,
    output logic       tx_dout,
    output logic       tx_pending,
    input  logic       rx_step,
    input  logic       rx_din,
    output logic       rx_valid,
    output logic       rx_dout,
    output logic       rx_err
);
    logic [3:0] trun, rrun;
    logic       tlast, rlast, rstuff;

    function automatic logic [3:0] inc_sat(input logic [3:0] v);
        inc_sat = (v == 4'hf) ? v : v + 4'd1;
    endfunction

    assign tx_pending = (trun == cfg_n) && ((cfg_mode[0] && tlast) || (cfg_mode[1] && !tlast));
    assign rstuff     = (rrun == cfg_n) && ((cfg_mode[0] && rlast) || (cfg_mode[1] && !rlast));
    assign tx_dout    = tx_pending ? ~tlast : tx_din;
    assign tx_take    = tx_step && !tx_pending;

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            trun <= '0; tlast <= 1'b0;
        end else if (clear) begin
            trun <= '0; tlast <= 1'b0;
        end else if (tx_step) begin
            trun  <= (tx_dout == tlast) ? inc_sat(trun) : 4'd1;
            tlast <= tx_dout;
        end

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            rrun <= '0; rlast <= 1'b0; rx_valid <= 1'b0; rx_dout <= 1'b0; rx_err <= 1'b0;
        end else if (clear) begin
            rrun <= '0; rlast <= 1'b0; rx_valid <= 1'b0; rx_err <= 1'b0;
        end else if (rx_step) begin
            rlast <= rx_din;
            if (rstuff) begin
                rx_valid <= 1'b0; rx_err <= rx_din == rlast; rrun <= 4'd1;
            end else begin
                rx_valid <= 1'b1; rx_dout <= rx_din; rx_err <= 1'b0;
                rrun     <= (rx_din == rlast) ? inc_sat(rrun) : 4'd1;
            end
        end else begin
            rx_valid <= 1'b0; rx_err <= 1'b0;
        end
endmodule
`default_nettype wire
