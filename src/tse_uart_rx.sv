`default_nettype none
// 8N1 receiver with 16x oversampling. rxd must already be synchronized.
// A falling edge starts a frame; the start bit is checked at its middle
// (8 ticks) so a glitch shorter than half a bit is ignored, then every bit
// is sampled 16 ticks later. valid or ferr pulses for one cycle in the middle
// of the stop bit, which leaves half a bit of slack for the next start edge.
module tse_uart_rx (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       tick16,
    input  logic       rxd,
    output logic       valid,
    output logic       ferr,
    output logic [7:0] data
);
    typedef enum logic [1:0] {IDLE, START, BITS, STOP} state_t;
    state_t     st;
    logic [3:0] phase;
    logic [2:0] nbit;
    logic       rxd_q;

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            st <= IDLE; phase <= '0; nbit <= '0; rxd_q <= 1'b1;
            valid <= 1'b0; ferr <= 1'b0; data <= '0;
        end else begin
            rxd_q <= rxd;
            valid <= 1'b0;
            ferr  <= 1'b0;
            case (st)
                IDLE:
                    if (rxd_q && !rxd) begin
                        st <= START; phase <= '0;
                    end
                START:
                    if (tick16) begin
                        phase <= phase + 4'd1;
                        if (phase == 4'd7) begin
                            phase <= '0; nbit <= '0;
                            if (rxd) st <= IDLE;
                            else     st <= BITS;
                        end
                    end
                BITS:
                    if (tick16) begin
                        phase <= phase + 4'd1;
                        if (phase == 4'd15) begin
                            data <= {rxd, data[7:1]};
                            nbit <= nbit + 3'd1;
                            if (nbit == 3'd7) st <= STOP;
                        end
                    end
                STOP:
                    if (tick16) begin
                        phase <= phase + 4'd1;
                        if (phase == 4'd15) begin
                            valid <= rxd;
                            ferr  <= !rxd;
                            st    <= IDLE;
                        end
                    end
                default: st <= IDLE;
            endcase
        end
endmodule
`default_nettype wire
