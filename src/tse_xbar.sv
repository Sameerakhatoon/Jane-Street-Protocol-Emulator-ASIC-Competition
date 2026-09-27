`default_nettype none
// Pin crossbar. Sources are the 16 lane virtual pins (lane x 4 + vpin; lanes
// that are not built read 0 and are never live), sinks are the NI lane inputs,
// pads are uo_out[7:0] (pads 0 to 7) and uio[7:0] (pads 8 to 15).
// Input side: 24 input sources, ui_in (0 to 7), uio_in (8 to 15) and the
// chip's own uo_out values (16 to 23), each synchronized exactly once: two
// rising-edge flops (sp) and a falling-edge capture plus two flops (sn, the
// same pin half a cycle earlier, for dual-edge timing). Sink config
// {inv, src[4:0]}; sources 24 to 31 read 0. With loopback (or the self-test
// pattern) the pad outputs replace ui_in and uio_in, pad p feeding source p.
// Output side, pad config {idle_oe, idle, en, od, inv, src[3:0]}: a pad
// that is not enabled outputs 0 and is released; a pad is idle while its
// source lane is not live or the source pin's output enable is 0, and then
// shows idle with oe = idle_oe (open drain: released when idle is 1, driven
// low when 0); open drain drives 0 or releases. ovr_en replaces a pad's
// value (trigger-out, interrupt) and drives it. Out and oe are registered
// with no reset, so the idle level holds through the reset at every mode
// switch.
module tse_xbar #(
    parameter int NI = 12
) (
    input  logic          clk,
    input  logic          rst_n,
    input  logic [16*9-1:0] cfg_out,
    input  logic [NI*6-1:0] cfg_in,
    input  logic          loopback,
    input  logic          lb_test,      // self-test: pads show lb_pat, not driven
    input  logic [15:0]   lb_pat,
    input  logic [15:0]   ovr_en,
    input  logic [15:0]   ovr_val,
    output logic [15:0]   pad_p,        // synchronized ui_in, uio_in (self-test, host SPI)
    output logic [15:0]   pad_n,
    input  logic [15:0]   src_out,
    input  logic [15:0]   src_oe,
    input  logic [15:0]   src_live,
    output logic [NI-1:0] sink_p,
    output logic [NI-1:0] sink_n,
    input  logic [15:0]   pin_in,       // {uio_in, ui_in}
    output logic [15:0]   pin_out,      // {uio_out, uo_out}
    output logic [15:0]   pin_oe
);
    logic [23:0] pin_eff, p1, sp, n1, n2, sn;
    logic [31:0] spx, snx;

    assign pin_eff = {pin_out[7:0], (loopback || lb_test) ? pin_out : pin_in};

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            p1 <= '0; sp <= '0; n2 <= '0; sn <= '0;
        end else begin
            p1 <= pin_eff; sp <= p1; n2 <= n1; sn <= n2;
        end
    always_ff @(negedge clk or negedge rst_n)
        if (!rst_n) n1 <= '0;
        else        n1 <= pin_eff;

    assign pad_p = sp[15:0];
    assign pad_n = sn[15:0];
    assign spx   = {8'd0, sp};
    assign snx   = {8'd0, sn};

    always_comb
        for (int i = 0; i < NI; i++) begin
            logic [5:0] c;
            c = cfg_in[6*i +: 6];
            sink_p[i] = spx[c[4:0]] ^ c[5];
            sink_n[i] = snx[c[4:0]] ^ c[5];
        end

    always_ff @(posedge clk)
        if (lb_test) begin
            pin_out <= lb_pat; pin_oe <= '0;
        end else begin
            for (int p = 0; p < 16; p++) begin
                logic [8:0] c;
                logic       o, idle;
                c    = cfg_out[9*p +: 9];
                o    = src_out[c[3:0]] ^ c[4];
                idle = !src_live[c[3:0]] || !src_oe[c[3:0]];
                if (ovr_en[p])  begin pin_out[p] <= ovr_val[p]; pin_oe[p] <= 1'b1;  end
                else if (!c[6]) begin pin_out[p] <= 1'b0;       pin_oe[p] <= 1'b0;  end
                else if (idle) begin
                    if (c[5])     begin pin_out[p] <= 1'b0;     pin_oe[p] <= !c[7]; end
                    else          begin pin_out[p] <= c[7];     pin_oe[p] <= c[8];  end
                end
                else if (c[5])  begin pin_out[p] <= 1'b0;       pin_oe[p] <= !o;    end
                else            begin pin_out[p] <= o;          pin_oe[p] <= 1'b1;  end
            end
        end
endmodule
`default_nettype wire
