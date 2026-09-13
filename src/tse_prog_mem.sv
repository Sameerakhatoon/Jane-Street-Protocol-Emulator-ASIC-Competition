`default_nettype none
// Program store: one IHP 512x32 SRAM shared by up to 4 lanes through fixed
// time slots. A free-running 2-bit slot counter gives lane i the port only
// when slot == i; a lane's grant depends on nothing but the slot counter and
// its own request, which is the non-interference property the formal
// proof checks. Lane i owns window i, words 128*i to 128*i+127 (256
// instructions, two per word), and receives whole words into its fetch
// buffer. Slots LANES..3 are the system slot: the host port while running
// (and, in 6x4, the data window 3); while stopped (run = 0) the host port
// owns every cycle.
module tse_prog_mem #(
    parameter int LANES = 3
) (
    input  logic               clk,
    input  logic               rst_n,
    input  logic               run,
    input  logic [LANES-1:0]   l_req,
    input  logic [LANES*7-1:0] l_addr,      // word address per lane, in its window
    output logic [LANES-1:0]   l_gnt,
    output logic [31:0]        l_rdata,     // valid the cycle after a grant
    input  logic               h_req,
    input  logic               h_we,
    input  logic [8:0]         h_addr,
    input  logic [31:0]        h_wdata,
    output logic               h_gnt,
    output logic [31:0]        h_rdata      // valid the cycle after a read grant
);
    logic [1:0]  slot;
    logic        lane_go, host_slot;
    logic [6:0]  la;
    logic [8:0]  addr;
    logic [31:0] dout;

    always_comb begin
        la = '0;
        for (int i = 0; i < LANES; i++)
            if (slot == 2'(i)) la = l_addr[7*i +: 7];
    end

    always_comb
        for (int i = 0; i < LANES; i++) l_gnt[i] = run && l_req[i] && slot == 2'(i);

    assign lane_go   = |l_gnt;
    assign host_slot = !run || 3'(slot) >= 3'(LANES);
    assign h_gnt     = h_req && host_slot;
    assign addr      = h_gnt ? h_addr : {slot, la};
    assign l_rdata   = dout;
    assign h_rdata   = dout;

    tse_sram_512x32 u_sram (
        .clk, .men(lane_go || h_gnt), .wen(h_gnt && h_we), .ren(lane_go || (h_gnt && !h_we)),
        .addr, .din(h_wdata), .dout
    );

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) slot <= '0;
        else        slot <= slot + 2'd1;
endmodule
`default_nettype wire
