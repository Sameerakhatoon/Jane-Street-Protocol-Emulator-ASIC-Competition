`default_nettype none
// Line codec NRZ / NRZI / Manchester, config as inputs.
// cfg_mode 0 NRZ, 1 NRZI, 2 Manchester; cfg_inv inverts; cfg_t_on selects
// whether NRZI toggles on a 1 or on a 0.
module tse_codec (
    input  logic       clk,
    input  logic       rst_n,
    input  logic [1:0] cfg_mode,
    input  logic       cfg_inv,
    input  logic       cfg_t_on,
    input  logic       tx_step,
    input  logic       tx_half,
    input  logic       tx_bit,
    input  logic       line_set,
    input  logic       line_val,
    output logic       tx_line,
    input  logic       rx_sample,
    input  logic       rx_s,
    input  logic       rx_q1,
    input  logic       rx_q1_s,
    output logic       rx_valid,
    output logic       rx_bit,
    output logic       rx_err
);
    localparam logic [1:0] NRZI = 2'd1, MAN = 2'd2;
    logic mbit, rprev, h1;

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            tx_line <= 1'b0; mbit <= 1'b0;
        end else if (line_set) begin
            tx_line <= line_val;
        end else if (tx_step) begin
            mbit <= tx_bit;
            case (cfg_mode)
                NRZI:    if (tx_bit == cfg_t_on) tx_line <= ~tx_line;
                MAN:     tx_line <= ~tx_bit ^ cfg_inv;
                default: tx_line <= tx_bit ^ cfg_inv;
            endcase
        end else if (tx_half && cfg_mode == MAN) begin
            tx_line <= mbit ^ cfg_inv;
        end

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            rprev <= 1'b0; h1 <= 1'b0; rx_valid <= 1'b0; rx_bit <= 1'b0; rx_err <= 1'b0;
        end else begin
            rx_valid <= rx_sample;
            rx_err   <= 1'b0;
            if (rx_q1) h1 <= rx_q1_s;
            if (rx_sample) begin
                rprev <= rx_s;
                case (cfg_mode)
                    NRZI:    rx_bit <= (rx_s != rprev) ? cfg_t_on : ~cfg_t_on;
                    MAN:     begin rx_bit <= rx_s ^ cfg_inv; rx_err <= h1 == rx_s; end
                    default: rx_bit <= rx_s ^ cfg_inv;
                endcase
            end
        end
endmodule
`default_nettype wire
