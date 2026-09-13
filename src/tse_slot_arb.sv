`default_nettype none
// Fixed-slot arbiter for the one single-port program SRAM. A free-running
// 4-bit count frames 16 cycles: lane i owns every cycle with cnt mod 4 == i
// (instruction fetch). With 3 lanes the fourth slot is the system slot: its
// occurrence k (cnt == 4k + 3) is lane k's data-window slot (LDW, STW, window
// 3) and occurrence 3 (cnt == 15) is the host's. While every lane is stopped
// the host owns every cycle. Each grant depends only on the count and the
// requester's own request, so no lane can change another lane's timing.
// With 4 lanes there is no system slot; the data store then needs its own
// arbiter with the same cnt == 4k + 3 rule.
module tse_slot_arb #(
    parameter int LANES = 3
) (
    input  logic                clk,
    input  logic                rst_n,
    input  logic                frame_sync,     // restart the count (RUN takes effect)
    input  logic [LANES-1:0]    req,            // instruction fetch
    input  logic [LANES*9-1:0]  addr,           // {window, word} per lane
    input  logic [LANES-1:0]    dreq,           // data window access
    input  logic [LANES-1:0]    dwe,
    input  logic [LANES*7-1:0]  daddr,
    input  logic [LANES*32-1:0] dwdata,
    input  logic                sys_req,
    input  logic                sys_we,
    input  logic [8:0]          sys_addr,
    input  logic [31:0]         sys_wdata,
    input  logic                all_stopped,
    output logic [3:0]          cnt,
    output logic [LANES-1:0]    gnt,
    output logic [LANES-1:0]    rvalid,         // memory output holds lane i's fetch
    output logic [LANES-1:0]    dgnt,
    output logic [LANES-1:0]    drvalid,        // memory output holds lane i's LDW word
    output logic                sys_gnt,
    output logic                sys_rvalid,
    output logic                mem_men,
    output logic                mem_wen,
    output logic                mem_ren,
    output logic [8:0]          mem_addr,
    output logic [31:0]         mem_din
);
    logic sys_slot, host_turn;
    assign sys_slot  = LANES < 4 && cnt[1:0] == 2'd3;
    assign host_turn = sys_slot && cnt[3:2] == 2'd3;

    always_comb begin
        for (int i = 0; i < LANES; i++) begin
            gnt[i]  = req[i] && cnt[1:0] == 2'(i);
            dgnt[i] = dreq[i] && sys_slot && cnt[3:2] == 2'(i);
        end
        sys_gnt  = sys_req && (all_stopped || host_turn);
        mem_addr = sys_addr;
        mem_din  = sys_wdata;
        mem_wen  = sys_gnt && sys_we;
        for (int i = 0; i < LANES; i++) begin
            if (gnt[i]) mem_addr = addr[9*i +: 9];
            if (dgnt[i]) begin
                mem_addr = {2'd3, daddr[7*i +: 7]};
                mem_din  = dwdata[32*i +: 32];
                mem_wen  = dwe[i];
            end
        end
        mem_men = |gnt || |dgnt || sys_gnt;
        mem_ren = mem_men && !mem_wen;
    end

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            cnt <= '0; rvalid <= '0; drvalid <= '0; sys_rvalid <= 1'b0;
        end else begin
            cnt        <= frame_sync ? 4'd0 : cnt + 4'd1;
            rvalid     <= gnt;
            drvalid    <= dgnt & ~dwe;
            sys_rvalid <= sys_gnt && !sys_we;
        end
endmodule
`default_nettype wire
