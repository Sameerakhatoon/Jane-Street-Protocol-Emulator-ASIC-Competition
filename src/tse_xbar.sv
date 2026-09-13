`default_nettype none
// Pin crossbar (config as inputs, plus a self-test loopback).
// NS output sources (lane virtual pins plus extras such as trigger-out),
// NI sink inputs (lane virtual pins), P input pads, PO output pads.
// Output pad config {idle_oe, idle, en, od, inv, src} (for
// 3 lanes src is 4 bits and the layout is bit-exact). Open drain drives 0 or
// releases; a pad that is not enabled outputs 0 and is released. A pad is
// idle while its source is not live (lane stopped) or the
// source pin's output enable is 0: it then shows idle with oe = idle_oe
// (open drain: released when idle is 1, driven low when 0). The output
// registers have no reset, so the idle level holds through the reset at
// every mode switch, the first cycle after power-up excepted.
// Input sink config {inv, pad}. Every pad is synchronized exactly once
// (two rising-edge flops, plus a falling-edge capture half a cycle earlier
// for dual-edge timing) and then multiplexed to the sinks.
module tse_xbar #(
    parameter int NS = 16,
    parameter int NI = 16,
    parameter int P  = 16,
    parameter int PO = 16,
    localparam int SW = $clog2(NS),
    localparam int PW = $clog2(P)
) (
    input  logic               clk,
    input  logic               rst_n,
    input  logic [PO*(SW+5)-1:0] cfg_out,
    input  logic [NI*(PW+1)-1:0] cfg_in,
    input  logic               lb_en,       // self-test: pads read lb_pat, pads not driven
    input  logic [PO-1:0]      lb_pat,
    output logic [P-1:0]       pad_p,       // synchronized pads, for self-test and host SPI
    output logic [P-1:0]       pad_n,
    input  logic [NS-1:0]      src_out,
    input  logic [NS-1:0]      src_oe,
    input  logic [NS-1:0]      src_live,
    output logic [NI-1:0]      sink_p,
    output logic [NI-1:0]      sink_n,
    input  logic [P-1:0]       pin_in,
    output logic [PO-1:0]      pin_out,
    output logic [PO-1:0]      pin_oe
);
    logic [P-1:0] pin_eff, p1, sp, n1, n2, sn;

    always_comb
        for (int p = 0; p < P; p++) pin_eff[p] = lb_en ? pin_out[p % PO] : pin_in[p];

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            p1 <= '0; sp <= '0; n2 <= '0; sn <= '0;
        end else begin
            p1 <= pin_eff; sp <= p1; n2 <= n1; sn <= n2;
        end
    assign pad_p = sp;
    assign pad_n = sn;
    always_ff @(negedge clk or negedge rst_n)
        if (!rst_n) n1 <= '0;
        else        n1 <= pin_eff;

    always_comb
        for (int i = 0; i < NI; i++) begin
            logic [PW:0] c;
            c = cfg_in[(PW+1)*i +: PW+1];
            sink_p[i] = sp[c[PW-1:0]] ^ c[PW];
            sink_n[i] = sn[c[PW-1:0]] ^ c[PW];
        end

    always_ff @(posedge clk)
        if (lb_en) begin
            pin_out <= lb_pat; pin_oe <= '0;
        end else begin
            for (int p = 0; p < PO; p++) begin
                logic [SW+4:0] c;
                logic          o, idle;
                c    = cfg_out[(SW+5)*p +: SW+5];
                o    = src_out[c[SW-1:0]] ^ c[SW];
                idle = !src_live[c[SW-1:0]] || !src_oe[c[SW-1:0]];
                if (!c[SW+2])    begin pin_out[p] <= 1'b0;      pin_oe[p] <= 1'b0;       end
                else if (idle) begin
                    if (c[SW+1])   begin pin_out[p] <= 1'b0;      pin_oe[p] <= !c[SW+3];  end
                    else           begin pin_out[p] <= c[SW+3];  pin_oe[p] <= c[SW+4];   end
                end
                else if (c[SW+1]) begin pin_out[p] <= 1'b0;     pin_oe[p] <= !o;        end
                else             begin pin_out[p] <= o;        pin_oe[p] <= 1'b1;       end
            end
        end
endmodule
`default_nettype wire
