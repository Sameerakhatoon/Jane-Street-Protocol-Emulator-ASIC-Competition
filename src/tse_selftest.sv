`default_nettype none
// Boot self-test, started by the BIST command (it destroys the SRAM
// contents, so the host runs it before writing the program).
// 1. March C- on the 512x32 program SRAM, twice, with the backgrounds
//    B = 0x00000000 and B = 0x55555555 (0 means B, 1 means ~B):
//    up(w0) up(r0,w1) up(r1,w0) down(r0,w1) down(r1,w0) up(r0)
//    A read is issued in one cycle and checked in the next, when the SRAM
//    output is valid; the write of the same address goes out in that cycle.
// 2. Pad loopback: walking one then walking zero on the 16 output registers,
//    looped inside the crossbar to the input synchronizers (pads not driven);
//    both the rising-edge and falling-edge samples must match.
// The first failure is kept and the test runs to the end, so its length is
// fixed (about 11,500 cycles). result = {fail, background, element, word};
// a loopback failure reports element 6. The result has no reset, so it
// survives the switch to run mode; clr (BEGIN) and start clear it.
module tse_selftest (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        start,
    input  logic        clr,
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
    output logic [13:0] result
);
    typedef enum logic [1:0] {IDLE, MARCH, LOOP} state_t;
    state_t      state;
    logic [2:0]  el, f_el;
    logic [8:0]  a, f_a;
    logic        bg, f_bg;
    logic        chk;                  // second cycle of a read: compare now
    logic [4:0]  k;
    logic [2:0]  w;
    logic [31:0] pat;

    logic up, has_rd, has_wr, rd_v, wr_v, last_a;
    always_comb begin
        up     = !(el == 3'd3 || el == 3'd4);
        has_rd = el != 3'd0;
        has_wr = el != 3'd5;
        rd_v   = el == 3'd2 || el == 3'd4;
        wr_v   = el == 3'd1 || el == 3'd3;
        last_a = up ? a == 9'd511 : a == 9'd0;
        pat    = bg ? 32'h5555_5555 : 32'h0;
    end

    assign busy   = state != IDLE;
    assign result = {done && !pass, f_bg, f_el, f_a};

    always_comb begin
        m_req = 1'b0; m_we = 1'b0; m_addr = a; m_wdata = wr_v ? ~pat : pat;
        if (state == MARCH) begin
            if (has_rd && !chk) m_req = 1'b1;                     // issue the read
            else if (has_wr) begin m_req = 1'b1; m_we = 1'b1; end // write (after the check)
        end
    end

    logic fail_now;
    assign fail_now = (state == MARCH && !(has_rd && !chk) && has_rd && m_rdata != (rd_v ? ~pat : pat))
                   || (state == LOOP && w == 3'd5 && (pad_p != lb_pat || pad_n != lb_pat));

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            state <= IDLE; el <= '0; a <= '0; bg <= 1'b0; chk <= 1'b0; k <= '0; w <= '0;
            lb_en <= 1'b0; lb_pat <= '0;
        end else begin
            unique case (state)
                IDLE: if (start) begin
                    state <= MARCH; el <= '0; a <= '0; bg <= 1'b0; chk <= 1'b0;
                end
                MARCH: begin
                    if (has_rd && !chk) chk <= 1'b1;
                    else begin
                        chk <= 1'b0;
                        if (!last_a) a <= up ? a + 9'd1 : a - 9'd1;
                        else if (el == 3'd5 && bg) begin
                            state <= LOOP; k <= '0; w <= '0; lb_en <= 1'b1; lb_pat <= 16'h0001;
                        end else if (el == 3'd5) begin
                            el <= '0; a <= '0; bg <= 1'b1;
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
                        k <= k + 5'd1;
                        lb_pat <= (k + 5'd1 < 5'd16) ? (16'd1 << (k + 5'd1)) : ~(16'd1 << (k - 5'd15));
                        if (k == 5'd31) begin state <= IDLE; lb_en <= 1'b0; end
                    end
                end
                default: state <= IDLE;
            endcase
        end

    always_ff @(posedge clk)
        if (clr || (state == IDLE && start)) begin
            done <= 1'b0; pass <= 1'b1; f_el <= '0; f_a <= '0; f_bg <= 1'b0;
        end else begin
            if (state == LOOP && w == 3'd5 && k == 5'd31) done <= 1'b1;
            if (fail_now && pass) begin
                pass <= 1'b0;
                f_el <= state == LOOP ? 3'd6 : el;
                f_a  <= state == LOOP ? 9'(k) : a;
                f_bg <= state == LOOP ? 1'b0 : bg;
            end
        end
endmodule
`default_nettype wire
