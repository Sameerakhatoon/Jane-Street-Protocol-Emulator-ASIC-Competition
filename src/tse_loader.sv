`default_nettype none
// Host command decoder: one parser for both front ends.
// Load mode (rst_n low): the byte bus. ld_data carries a byte, every change of
// ld_strobe delivers it, ld_frame = 1 marks a command byte, ld_dout shows the
// current readback byte. The strobe is synchronized and edge detected; data
// and frame are read on that edge, after the host has held them stable.
// Run mode: the same bytes arrive from the SPI target (s_valid, s_first =
// first byte after CS falls) and ld_dout feeds its MISO shift register.
// Commands, multi-byte fields most significant byte first:
//   0x01 WR   addr(2) data(4)          0x02 RD   addr(2), then 4 strobes
//   0x03 WRB  addr(2) n(2) data(4n)    0x04 RDB  addr(2) n(2), then 4n strobes
//   0x10 BEGIN   clear loaded and host error, stop lanes, CRC to all ones, clear config
//   0x11 BIST    start the SRAM self-test
//   0x12 COMMIT  crc(4): loaded = (crc == running CRC-32 of every WR/WRB address and data byte)
//   0x13 STAT    present STATUS[7:0]
// A command byte always restarts the parser, so a random power-up state, a
// truncated command or a strobe outside a command does nothing. A read word
// is fetched as soon as its address is known (and the next one of a burst
// when the last byte of the previous one is presented), so every strobe
// presents its byte the next cycle. In run mode BEGIN, BIST and COMMIT are
// refused unless every lane is stopped; a refusal sets the sticky host error.
// loaded, host_err and the CRC have no reset: loaded clears on the first
// cycle of load mode, the other two at BEGIN. The parser is reset for two
// cycles at every mode switch and the strobe synchronizer then refills, so
// a strobe edge less than 3 clocks after rst_n falls is lost; hosts wait 4
// or more.
module tse_loader (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        load_mode,
    input  logic [7:0]  ld_data,
    input  logic        ld_strobe,
    input  logic        ld_frame,
    output logic [7:0]  ld_dout,
    input  logic        s_valid,
    input  logic        s_first,
    input  logic [7:0]  s_data,
    output logic        h_we,
    output logic        h_re,
    output logic [15:0] h_addr,
    output logic [31:0] h_wdata,
    input  logic [31:0] h_rdata,
    input  logic        h_rvalid,
    input  logic        h_err,
    input  logic        lanes_idle,
    input  logic        bist_done,
    input  logic        bist_pass,
    input  logic [3:0]  halted,
    output logic        loaded,
    output logic        host_err,
    output logic [31:0] load_crc,
    output logic        cfg_clr,
    output logic        bist_start
);
    localparam logic [7:0] C_WR = 8'h01, C_RD = 8'h02, C_WRB = 8'h03, C_RDB = 8'h04;
    localparam logic [7:0] C_BEGIN = 8'h10, C_BIST = 8'h11, C_COMMIT = 8'h12, C_STAT = 8'h13;
    localparam logic [2:0] P_IDLE = 3'd0, P_AHI = 3'd1, P_ALO = 3'd2, P_NHI = 3'd3,
                           P_NLO = 3'd4, P_DATA = 3'd5, P_READ = 3'd6, P_CRC = 3'd7;

    // CRC-32/ISO-HDLC, reflected: poly 0xEDB88320, init and final XOR all ones
    function automatic logic [31:0] crc32_byte(input logic [31:0] c, input logic [7:0] b);
        logic [31:0] x;
        x = c ^ {24'h0, b};
        for (int i = 0; i < 8; i++) x = x[0] ? ((x >> 1) ^ 32'hEDB88320) : (x >> 1);
        crc32_byte = x;
    endfunction

    logic [2:0]  st_sync;
    logic [1:0]  prime;                  // edges ignored until the synchronizer has filled
    logic        bv, bf;
    logic [7:0]  bd;
    logic [2:0]  ph;
    logic [7:0]  cmd;
    logic [15:0] count;
    logic [1:0]  nbyte;
    logic [23:0] wbuf;
    logic [31:0] rbuf, rsrc, crc;
    logic        lm_q;

    assign bv = load_mode ? (prime == 2'd3 && (st_sync[2] ^ st_sync[1])) : s_valid;
    assign bf = load_mode ? ld_frame : s_first;
    assign bd = load_mode ? ld_data : s_data;
    assign load_crc = ~crc;
    assign rsrc     = h_rvalid ? h_rdata : rbuf;   // a word may arrive with the strobe that shows it

    logic is_wr, is_burst, may_ctl, c_begin, c_commit_ok, c_refuse, crc_byte;
    assign is_wr    = cmd == C_WR || cmd == C_WRB;
    assign is_burst = cmd == C_WRB || cmd == C_RDB;
    assign may_ctl  = load_mode || lanes_idle;
    assign c_begin  = bv && bf && bd == C_BEGIN && may_ctl;
    assign c_refuse = bv && bf && (bd == C_BEGIN || bd == C_BIST || bd == C_COMMIT) && !may_ctl;
    assign crc_byte = bv && !bf && (ph == P_DATA || (is_wr && (ph == P_AHI || ph == P_ALO)));
    assign c_commit_ok = bv && !bf && ph == P_CRC && nbyte == 2'd3;

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            st_sync <= '0; prime <= '0; ph <= P_IDLE; cmd <= '0; count <= '0; nbyte <= '0;
            wbuf <= '0; rbuf <= '0; ld_dout <= '0; h_we <= 1'b0; h_re <= 1'b0; h_addr <= '0;
            h_wdata <= '0; cfg_clr <= 1'b0; bist_start <= 1'b0;
        end else begin
            st_sync    <= {st_sync[1:0], ld_strobe};
            if (prime != 2'd3) prime <= prime + 2'd1;
            h_we       <= 1'b0;
            h_re       <= 1'b0;
            cfg_clr    <= 1'b0;
            bist_start <= 1'b0;
            if (h_rvalid) rbuf <= h_rdata;
            if (h_we || h_re) h_addr <= h_addr + 16'd1;    // bursts advance after each word
            if (bv && bf) begin
                cmd   <= bd;
                nbyte <= 2'd0;
                ph    <= P_IDLE;
                unique case (bd)
                    C_WR, C_RD, C_WRB, C_RDB: ph <= P_AHI;
                    C_BEGIN:  cfg_clr <= may_ctl;
                    C_BIST:   bist_start <= may_ctl;
                    C_COMMIT: if (may_ctl) ph <= P_CRC;
                    C_STAT:   ld_dout <= {halted, host_err, bist_pass, bist_done, loaded};
                    default:  ;
                endcase
            end else if (bv) begin
                unique case (ph)
                    P_AHI: begin h_addr[15:8] <= bd; ph <= P_ALO; end
                    P_ALO: begin
                        h_addr[7:0] <= bd;
                        count <= 16'd1;
                        if (is_burst)   ph <= P_NHI;
                        else if (is_wr) ph <= P_DATA;
                        else begin h_re <= 1'b1; ph <= P_READ; end
                    end
                    P_NHI: begin count[15:8] <= bd; ph <= P_NLO; end
                    P_NLO: begin
                        count[7:0] <= bd;
                        if ({count[15:8], bd} == 16'd0) ph <= P_IDLE;
                        else if (is_wr)                 ph <= P_DATA;
                        else begin h_re <= 1'b1; ph <= P_READ; end
                    end
                    P_DATA: begin
                        nbyte <= nbyte + 2'd1;
                        wbuf  <= {wbuf[15:0], bd};
                        if (nbyte == 2'd3) begin
                            h_we    <= 1'b1;
                            h_wdata <= {wbuf, bd};
                            count   <= count - 16'd1;
                            if (count == 16'd1) ph <= P_IDLE;
                        end
                    end
                    P_READ: begin
                        nbyte   <= nbyte + 2'd1;
                        ld_dout <= rsrc[31:24];
                        rbuf    <= {rsrc[23:0], 8'h00};
                        if (nbyte == 2'd3) begin
                            count <= count - 16'd1;
                            if (count == 16'd1) ph <= P_IDLE;
                            else                h_re <= 1'b1;
                        end
                    end
                    P_CRC: begin
                        nbyte <= nbyte + 2'd1;
                        wbuf  <= {wbuf[15:0], bd};
                        if (nbyte == 2'd3) ph <= P_IDLE;
                    end
                    default: ph <= P_IDLE;
                endcase
            end
        end

    always_ff @(posedge clk) begin
        lm_q <= load_mode;
        if (load_mode && !lm_q) loaded <= 1'b0;
        else if (c_begin)       loaded <= 1'b0;
        else if (c_commit_ok)   loaded <= {wbuf, bd} == ~crc;
        if (c_begin)                host_err <= 1'b0;
        else if (c_refuse || h_err) host_err <= 1'b1;
        if (c_begin)       crc <= 32'hFFFFFFFF;
        else if (crc_byte) crc <= crc32_byte(crc, bd);
    end
endmodule
`default_nettype wire
