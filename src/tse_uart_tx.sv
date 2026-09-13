`default_nettype none
// 8N1 transmitter. tick16 runs at 16 times the baud rate; a frame starts on
// the first tick16 after start and each symbol lasts 16 ticks, so a frame
// accepted right after a receiver's valid pulse never drifts against it.
// txd comes straight from a flop and idles high.
module tse_uart_tx (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       tick16,
    input  logic       start,
    input  logic [7:0] data,
    output logic       busy,
    output logic       txd
);
    logic [8:0] sh;         // {data, start bit}; the stop bit is shifted in as 1
    logic [3:0] left;       // symbols still to send after the current one
    logic [3:0] phase;      // tick16 count inside the current symbol
    logic       pend;       // start accepted, waiting for the first tick16
    logic       sending;

    assign busy = pend || sending;

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            sh <= '1; left <= '0; phase <= '0; pend <= 1'b0; sending <= 1'b0; txd <= 1'b1;
        end else if (start && !busy) begin
            sh <= {data, 1'b0}; pend <= 1'b1;
        end else if (tick16 && pend) begin
            pend <= 1'b0; sending <= 1'b1; left <= 4'd9; phase <= 4'd0;
            txd <= sh[0]; sh <= {1'b1, sh[8:1]};
        end else if (tick16 && sending) begin
            phase <= phase + 4'd1;
            if (phase == 4'd15) begin
                if (left == 4'd0) begin
                    sending <= 1'b0; txd <= 1'b1;
                end else begin
                    left <= left - 4'd1; txd <= sh[0]; sh <= {1'b1, sh[8:1]};
                end
            end
        end
endmodule
`default_nettype wire
