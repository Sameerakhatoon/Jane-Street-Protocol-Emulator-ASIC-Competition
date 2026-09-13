`default_nettype none
// Reset generation from the one rst_n pin. Both resets assert asynchronously
// and release synchronously (two flops), so every flop leaves reset in the
// same cycle.
//   eng_rst_n: engines (lanes, event unit, host SPI). Asserted
//              while rst_n is low, which is load mode.
//   ld_rst_n:  loader. Asserted while rst_n is high (run mode), so the
//              loader always starts a load session from its idle state.
//   sys_rst_n: shared blocks, see below.
module tse_rst_sync (
    input  logic clk,
    input  logic rst_n,
    output logic eng_rst_n,
    output logic ld_rst_n,
    output logic sys_rst_n,
    output logic load_mode               // same timing as ld_rst_n, for logic use
);
    logic e0, l0;

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin e0 <= 1'b0; eng_rst_n <= 1'b0; end
        else        begin e0 <= 1'b1; eng_rst_n <= e0;   end

    always_ff @(posedge clk or posedge rst_n)
        if (rst_n)  begin l0 <= 1'b0; ld_rst_n <= 1'b0; load_mode <= 1'b0; end
        else        begin l0 <= 1'b1; ld_rst_n <= l0;   load_mode <= l0;   end

    // Shared infrastructure (config bank write logic, program memory, crossbar)
    // works in both modes and is reset for two cycles at every mode switch:
    // one input asserts asynchronously while the other is still asserted, and
    // release always comes from one synchronizer flop, so the OR is glitch-free.
    assign sys_rst_n = eng_rst_n | ld_rst_n;
endmodule
`default_nettype wire
