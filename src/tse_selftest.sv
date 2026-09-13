`default_nettype none
// Boot self-test, started by the loader in load mode (it destroys the SRAM
// contents, so it runs before the program is written).
// 1. March C- on the 512x32 program SRAM with solid backgrounds:
//    up(w0) up(r0,w1) up(r1,w0) down(r0,w1) down(r1,w0) up(r0)
//    A read is issued in one cycle and checked in the next, when the SRAM
//    output is valid; the write of the same address goes out in that cycle.
// 2. Pad loopback: walking one then walking zero on the 16 output registers,
//    looped inside the crossbar to the input synchronizers (pads not driven);
//    both the rising-edge and falling-edge samples must match.
// info = {2'b00, pass, done, element[2:0], fail_addr[8:0], loop_fail_bits[15:0]}
module tse_selftest (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        start,
    output logic        m_req,
    output logic        m_we,
    output logic [8:0]  m_addr,
    output logic [31:0] m_wdata,
    input  logic [31:0] m_rdata,
    output logic        lb_en,
    output logic [15:0] lb_pat,
    input  logic [15:0] pad_p,
    input  logic [15:0] pad_n,
    output logic        busy,
    output logic        done,
    output logic        pass,
    output logic [31:0] info
);
    typedef enum logic [1:0] {IDLE, MARCH, LOOP} state_t;
    state_t      state;
    logic [2:0]  el;
    logic [8:0]  a, fail_a;
    logic        chk;                  // second cycle of a read: compare now
    logic [4:0]  k;
    logic [2:0]  w;
    logic [15:0] lfail;

    logic up, has_rd, has_wr, rd_v, wr_v, last_a;
    always_comb begin
        up     = !(el == 3'd3 || el == 3'd4);
        has_rd = el != 3'd0;
        has_wr = el != 3'd5;
        rd_v   = el == 3'd2 || el == 3'd4;
        wr_v   = el == 3'd1 || el == 3'd3;
        last_a = up ? a == 9'd511 : a == 9'd0;
    end

    assign busy = state != IDLE;
    assign info = {2'b00, pass, done, el, fail_a, lfail};

    always_comb begin
        m_req = 1'b0; m_we = 1'b0; m_addr = a; m_wdata = {32{wr_v}};
        if (state == MARCH) begin
            if (has_rd && !chk) m_req = 1'b1;                     // issue the read
            else if (has_wr) begin m_req = 1'b1; m_we = 1'b1; end // write (after the check)
        end
    end

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            state <= IDLE; el <= '0; a <= '0; fail_a <= '0; chk <= 1'b0; k <= '0; w <= '0;
            lfail <= '0; lb_en <= 1'b0; lb_pat <= '0; done <= 1'b0; pass <= 1'b0;
        end else begin
            unique case (state)
                IDLE: if (start) begin
                    state <= MARCH; el <= '0; a <= '0; chk <= 1'b0; done <= 1'b0; pass <= 1'b1; lfail <= '0;
                end
                MARCH: begin
                    if (has_rd && !chk) chk <= 1'b1;
                    else begin
                        chk <= 1'b0;
                        if (has_rd && m_rdata != {32{rd_v}} && pass) begin
                            pass <= 1'b0; fail_a <= a;
                        end
                        if (!last_a) a <= up ? a + 9'd1 : a - 9'd1;
                        else if (el == 3'd5) begin
                            state <= LOOP; k <= '0; w <= '0; lb_en <= 1'b1; lb_pat <= 16'h0001;
                        end else begin
                            el <= el + 3'd1;
                            a  <= (el == 3'd2 || el == 3'd3) ? 9'd511 : 9'd0;   // next element's start
                        end
                    end
                end
                LOOP: begin
                    w <= w + 3'd1;
                    if (w == 3'd5) begin                                  // output reg + sync settled
                        w <= '0;
                        if (pad_p != lb_pat || pad_n != lb_pat) begin
                            pass <= 1'b0; lfail <= lfail | (pad_p ^ lb_pat) | (pad_n ^ lb_pat);
                        end
                        k <= k + 5'd1;
                        lb_pat <= (k + 5'd1 < 5'd16) ? (16'd1 << (k + 5'd1)) : ~(16'd1 << (k - 5'd15));
                        if (k == 5'd31) begin state <= IDLE; lb_en <= 1'b0; done <= 1'b1; end
                    end
                end
                default: state <= IDLE;
            endcase
        end
endmodule
`default_nettype wire
