`default_nettype none
// Program store and data window. One IHP 512x32 SRAM is shared by the lanes
// through tse_slot_arb's fixed slots: a free-running count cnt frames 16
// cycles, lane i fetches in cycles with cnt mod 4 == i. Lane i reads window
// l_win[i] (128 words, two instructions per word) or, with l_fb[i] set, the
// 8-word fallback store, in the same slot and with the same one-cycle
// latency, so a program behaves identically from either; a fallback fetch
// leaves the SRAM idle, so a dead macro does not matter to it.
// Data window (LDW, STW), 128 word addresses: lane k owns the data cycles
// with cnt == 4k + 3, occurrences of the system slot, and the window is SRAM
// window 3; the host has the system slot at cnt == 15. While every lane is
// stopped the host may use the
// SRAM in any cycle. frame_end (cnt == 15) closes the 16-cycle frame: RUN
// changes there, so a lane's data cycles have a fixed phase from its start.
// Lane data port, one per lane (the lane drives the first four):
//   d_req    hold from the dispatch of LDW or STW until d_gnt
//   d_we     1 for STW
//   d_addr   word address in the data window (ind ? X[6:0] : addr)
//   d_wdata  ISR, for STW
//   d_gnt    the access happens in this cycle (STW writes now)
//   d_rdata  shared; holds the LDW word in the cycle after d_gnt
// Host port: one access at a time, held until h_gnt; h_dw selects the data
// window and h_fb the fallback store (h_addr[6:0] and [2:0]), else the SRAM
// word h_addr. Read data is valid the cycle after h_gnt. h_wdata must hold
// until two cycles after h_gnt (the latch stores write from it).
// Self-test: t_en switches the macro to its test port; the caller keeps the
// functional port idle while it does.
module tse_prog_mem #(
    parameter int LANES    = 2,
    parameter bit LATCH    = 1
) (
    input  logic                clk,
    input  logic                rst_n,
    input  logic                clr,
    input  logic                all_stopped,
    input  logic [LANES-1:0]    l_req,
    input  logic [LANES*7-1:0]  l_addr,
    input  logic [LANES*2-1:0]  l_win,
    input  logic [LANES-1:0]    l_fb,
    output logic [LANES-1:0]    l_gnt,
    output logic [31:0]         l_rdata,
    input  logic [LANES-1:0]    d_req,
    input  logic [LANES-1:0]    d_we,
    input  logic [LANES*7-1:0]  d_addr,
    input  logic [LANES*32-1:0] d_wdata,
    output logic [LANES-1:0]    d_gnt,
    output logic [31:0]         d_rdata,
    input  logic                h_req,
    input  logic                h_we,
    input  logic                h_dw,
    input  logic                h_fb,
    input  logic [8:0]          h_addr,
    input  logic [31:0]         h_wdata,
    output logic                h_gnt,
    output logic [31:0]         h_rdata,
    input  logic                t_en,
    input  logic                t_req,
    input  logic                t_we,
    input  logic [8:0]          t_addr,
    input  logic [31:0]         t_wdata,
    output logic [3:0]          cnt,
    output logic                frame_end
);
    logic [LANES-1:0]   s_req, f_gnt, s_gnt, s_rv, s_drv, s_dgnt;
    logic [LANES*9-1:0] s_addr;
    logic               sys_req, sys_gnt, sys_rv;
    logic [8:0]         sys_addr;
    logic               men, wen, ren;
    logic [8:0]         maddr;
    logic [31:0]        mdin, dout;

    // ---------------- fallback store
    logic        fb_hw, fb_hr, fb_v;
    logic [2:0]  fb_ra, fa;
    logic [31:0] fb_rd;
    logic        fb_busy;
    logic [255:0] fb_q;
    always_comb begin
        fa = '0;
        for (int i = 0; i < LANES; i++) begin
            f_gnt[i] = l_req[i] && l_fb[i] && cnt[1:0] == 2'(i);
            s_req[i] = l_req[i] && !l_fb[i];
            s_addr[9*i +: 9] = {l_win[2*i +: 2], l_addr[7*i +: 7]};
            if (f_gnt[i]) fa = l_addr[7*i +: 3];
        end
    end
    assign fb_hw = h_req && h_fb && h_we && !fb_busy;
    assign fb_hr = h_req && h_fb && !h_we && !fb_busy && f_gnt == '0;

    tse_cfg_bank #(.WORDS(8), .LATCH(LATCH), .WD_REG(0)) u_fb (
        .clk, .rst_n, .clr, .we(fb_hw), .addr(h_addr[2:0]), .wdata(h_wdata), .busy(fb_busy),
        .rd_addr(fb_ra), .rd_data(fb_rd), .q(fb_q)
    );

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            fb_ra <= '0; fb_v <= 1'b0;
        end else begin
            fb_v <= f_gnt != '0;
            if (f_gnt != '0) fb_ra <= fa;
            else if (fb_hr)  fb_ra <= h_addr[2:0];
        end

    // ---------------- SRAM slots
    tse_slot_arb #(.LANES(LANES)) u_arb (
        .clk, .rst_n, .frame_sync(1'b0),
        .req(s_req), .addr(s_addr),
        .dreq(d_req), .dwe(d_we), .daddr(d_addr), .dwdata(d_wdata),
        .sys_req, .sys_we(h_we), .sys_addr, .sys_wdata(h_wdata), .all_stopped,
        .cnt, .gnt(s_gnt), .rvalid(s_rv), .dgnt(s_dgnt), .drvalid(s_drv),
        .sys_gnt, .sys_rvalid(sys_rv),
        .mem_men(men), .mem_wen(wen), .mem_ren(ren), .mem_addr(maddr), .mem_din(mdin)
    );

    tse_sram_512x32 u_sram (
        .clk, .men, .wen, .ren, .addr(maddr), .din(mdin),
        .t_en, .t_men(t_req), .t_wen(t_we), .t_addr, .t_din(t_wdata), .dout
    );

    assign l_gnt     = s_gnt | f_gnt;
    assign l_rdata   = fb_v ? fb_rd : dout;
    assign frame_end = cnt == 4'd15;

    // ---------------- data window and host
    logic hfb_q;                       // the last host read was from the fallback store
    assign sys_req  = h_req && !h_fb;
    assign sys_addr = h_dw ? {2'd3, h_addr[6:0]} : h_addr;
    assign d_gnt    = s_dgnt;
    assign d_rdata  = dout;
    assign h_gnt    = sys_gnt || fb_hw || fb_hr;

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n)     hfb_q <= 1'b0;
        else if (h_gnt) hfb_q <= h_fb;

    assign h_rdata = hfb_q ? fb_rd : dout;

    wire _unused = &{s_rv, s_drv, s_dgnt, sys_rv, fb_busy, fb_q, 1'b0};
endmodule
`default_nettype wire
