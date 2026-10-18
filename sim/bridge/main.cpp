// tse_sim: one Verilated top plus the adapters picked on the command line.
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "harness.h"
#include "uart_pty.h"
#include "eth_tap.h"
#include "serprog.h"
#include "rbb.h"
#include "can_node.h"

// Per-top pin defaults. The top is chosen at build time (make TOP=...).
#if defined(TSE_TOP_UART)
static const char* TOP_NAME = "uart";     // UART test top transmitter, no RX
static const char* DEF_TX   = "uo0";
static const char* DEF_IN   = "bus";
#elif defined(TSE_TOP_ECHO)
static const char* TOP_NAME = "echo";     // sim/rtl/tt_um_uart_echo.sv stand-in
static const char* DEF_TX   = "uo0";
static const char* DEF_IN   = "ui0";
#else
static const char* TOP_NAME = "chip";     // full chip: pins depend on the loaded spec
static const char* DEF_TX   = "uo0";
static const char* DEF_IN   = "ui0";
#endif

static volatile int g_stop = 0;

static void on_signal(int sig){
    (void)sig;
    g_stop = 1;
}

static double now_s(void){
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

static double cpu_s(void){
    struct timespec t;
    clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

static void usage(void){
    fprintf(stderr,
        "usage: tse_sim [options]   (top: %s, model clock 60 MHz)\n"
        "  --uart                 pty bridge, prints 'pty /dev/pts/N'\n"
        "  --baud N               UART baud (115200)\n"
        "  --uart-tx PIN          chip TX pin to decode (%s)\n"
        "  --uart-rx PIN|bus      chip RX pin to drive, or the ui_in byte bus (%s)\n"
        "  --tap IF               Ethernet bridge on Linux TAP IF (rd ui1, td uo1)\n"
        "  --pcap FILE            record TAP frames with model-time stamps\n"
        "  --serprog PORT         flashrom serprog on 127.0.0.1:PORT (0 = any)\n"
        "  --rbb PORT             OpenOCD remote_bitbang on 127.0.0.1:PORT (0 = any)\n"
        "  --rbb-settle N         clocks per pin write (4: TCK = 7.5 MHz)\n"
        "  --jtag TCK,TMS,TDI,TDO JTAG pins (ui2,ui3,ui4,uo3)\n"
        "  --swd SWCLK,SWDIO      SWD pins, SWDIO a uio pin (ui5,uio2)\n"
        "  --can IF|unix:PATH     CAN node on SocketCAN IF or a can_frame socket (twice: two nodes)\n"
        "  --can-pins TX,RX       chip CAN TX (uo or uio) and RX pins (uio7,ui6)\n"
        "  --fst FILE             FST waveform of the whole model\n"
        "  --fst-from C           first cycle to dump (0)\n"
        "  --fst-cycles N         cycles to dump (0 = until exit)\n"
        "  --vcd FILE             pins-only VCD for sigrok (1 ns timescale)\n"
        "  --free-run             run time even when no adapter is busy\n"
        "  --cycles N             stop after N cycles (0 = until SIGINT or SIGTERM)\n"
        "  --bench N              run N cycles flat out, print cycles per second\n"
        "  --pin-server           pins from stdin: S ui uio rst, T n, R, U n mask, Q (hex)\n"
        "  --quantum N            cycles per batch between poll() calls (4096)\n"
        "PIN is ui0..ui7, uo0..uo7, uio0..uio7 or a pinout name (XIO2, RUN.SPI_MISO).\n",
        TOP_NAME, DEF_TX, DEF_IN);
}

// Pin server: a host program (the loader CLI) owns the pins over a pipe.
//   S <ui_in> <uio_in> <rst_n>   set inputs, no reply
//   T <n>                        run n cycles, no reply
//   R                            reply "<uo_out>\n"
//   U <n> <mask>                 run up to n cycles, stopping after the first one in
//                                which uio_out or uio_oe changed under mask; reply
//                                "<cycles run> <uo_out> <uio_out> <uio_oe>\n"
//   Q                            quit
// All numbers hex. Adapters still tick, but not while rst_n is low.
static int pin_server(Harness* h){
    char line[128];
    while(fgets(line, sizeof(line), stdin)){
        unsigned a, b, c;
        unsigned long long n;
        switch(line[0]){
        case 'S':
            if(sscanf(line + 1, "%x %x %x", &a, &b, &c) != 3)
                goto bad;
            h->pins.ui_in = (uint8_t)a;
            h->pins.uio_in = (uint8_t)b;
            h->pins.rst_n = c ? 1 : 0;
            break;
        case 'T':
            if(sscanf(line + 1, "%llx", &n) != 1)
                goto bad;
            while(n--)
                harness_step(h, h->pins.rst_n);
            break;
        case 'R':
            printf("%x\n", h->pins.uo_out);
            fflush(stdout);
            break;
        case 'U': {
            // a host-side bus model (open drain) sleeps until the chip moves a line
            unsigned long long ran = 0;
            if(sscanf(line + 1, "%llx %x", &n, &a) != 2)
                goto bad;
            uint8_t o0 = h->pins.uio_out & a, e0 = h->pins.uio_oe & a;
            while(ran < n){
                harness_step(h, h->pins.rst_n);
                ran++;
                if((h->pins.uio_out & a) != o0 || (h->pins.uio_oe & a) != e0)
                    break;
            }
            printf("%llx %x %x %x\n", ran, h->pins.uo_out, h->pins.uio_out, h->pins.uio_oe);
            fflush(stdout);
            break;
        }
        case 'Q':
            return 0;
        case '\n':
            break;
        default:
            goto bad;
        }
        continue;
    bad:
        fprintf(stderr, "pin-server: bad line: %s", line);
        return 1;
    }
    return 0;
}

static int parse_pin(const char* s, PinRef* r){
    if(pin_lookup(s, r, NULL) == 0)
        return 0;
    fprintf(stderr, "bad pin name: %s\n", s);
    return -1;
}

// Where each host program should connect, one line per adapter.
static void announce(FILE* f, Adapter* uart, Adapter* sp, Adapter* rbb, Adapter** can){
    if(uart)
        fprintf(f, "pty %s\n", uart_pty_path(uart));
    if(sp)
        fprintf(f, "serprog 127.0.0.1:%d\n", serprog_port_of(sp));
    if(rbb)
        fprintf(f, "rbb 127.0.0.1:%d\n", rbb_port_of(rbb));
    for(int i = 0; i < 2; i++)
        if(can[i])
            fprintf(f, "can %c %s\n", 'A' + i, can_node_where(can[i]));
    fflush(f);
}

// "a,b,c" -> exactly n pins
static int parse_pins(const char* s, PinRef* r, int n){
    char buf[128];
    int  k = 0;
    snprintf(buf, sizeof(buf), "%s", s);
    for(char* t = strtok(buf, ","); t; t = strtok(NULL, ",")){
        if(k == n || parse_pin(t, &r[k]) < 0)
            break;
        k++;
    }
    if(k == n)
        return 0;
    fprintf(stderr, "want %d pins in '%s'\n", n, s);
    return -1;
}

int main(int argc, char** argv){
    int         want_uart = 0;
    uint32_t    baud = 115200;
    const char* tx_name = DEF_TX;
    const char* in_name = DEF_IN;
    const char* tap_if = NULL;
    const char* pcap_path = NULL;
    int         serprog_port = -1;
    const char* fst_path = NULL;
    uint64_t    fst_from = 0, fst_cycles = 0, bench = 0;
    int         quantum = 0;
    int         free_run = 0;
    const char* vcd_path = NULL;
    uint64_t    stop_at = 0;
    int         pin_srv = 0;
    int         rbb_port = -1, rbb_settle = 4;
    const char* jtag_pins = "ui2,ui3,ui4,uo3";
    const char* swd_pins = "ui5,uio2";
    const char* can_host[2] = {NULL, NULL};
    int         n_can = 0;
    const char* can_pins = "uio7,ui6";

    for(int i = 1; i < argc; i++){
        const char* o = argv[i];
        const char* v = i + 1 < argc ? argv[i + 1] : NULL;
        if(!strcmp(o, "--uart")){ want_uart = 1; continue; }
        if(!strcmp(o, "--free-run")){ free_run = 1; continue; }
        if(!strcmp(o, "--pin-server")){ pin_srv = 1; continue; }
        if(!strcmp(o, "--help") || !strcmp(o, "-h")){ usage(); return 0; }
        if(!v || strncmp(o, "--", 2)){ usage(); return 2; }
        i++;
        if(!strcmp(o, "--baud"))             baud = (uint32_t)strtoul(v, NULL, 0);
        else if(!strcmp(o, "--uart-tx"))     tx_name = v;
        else if(!strcmp(o, "--uart-rx"))     in_name = v;
        else if(!strcmp(o, "--tap"))         tap_if = v;
        else if(!strcmp(o, "--pcap"))        pcap_path = v;
        else if(!strcmp(o, "--serprog"))     serprog_port = atoi(v);
        else if(!strcmp(o, "--fst"))         fst_path = v;
        else if(!strcmp(o, "--fst-from"))    fst_from = strtoull(v, NULL, 0);
        else if(!strcmp(o, "--fst-cycles"))  fst_cycles = strtoull(v, NULL, 0);
        else if(!strcmp(o, "--vcd"))         vcd_path = v;
        else if(!strcmp(o, "--cycles"))      stop_at = strtoull(v, NULL, 0);
        else if(!strcmp(o, "--bench"))       bench = strtoull(v, NULL, 0);
        else if(!strcmp(o, "--quantum"))     quantum = atoi(v);
        else if(!strcmp(o, "--rbb"))         rbb_port = atoi(v);
        else if(!strcmp(o, "--rbb-settle"))  rbb_settle = atoi(v);
        else if(!strcmp(o, "--jtag"))        jtag_pins = v;
        else if(!strcmp(o, "--swd"))         swd_pins = v;
        else if(!strcmp(o, "--can-pins"))    can_pins = v;
        else if(!strcmp(o, "--can") && n_can < 2) can_host[n_can++] = v;
        else { usage(); return 2; }
    }
    if(!baud){
        fprintf(stderr, "baud must be > 0\n");
        return 2;
    }

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    signal(SIGPIPE, SIG_IGN);
    setvbuf(stdout, NULL, _IOLBF, 0);

    Harness h;
    harness_init(&h, argc, argv);
    if(quantum > 0)
        h.quantum = quantum;
    h.free_run = free_run;
    h.stop_at = stop_at;
    if(vcd_path && harness_vcd(&h, vcd_path) < 0)
        return 1;
    if(fst_path && harness_trace(&h, fst_path, fst_from, fst_cycles) < 0)
        return 1;

    Adapter* uart = NULL;
    if(want_uart){
        UartCfg c;
        memset(&c, 0, sizeof(c));
        c.baud = baud;
        if(parse_pin(tx_name, &c.tx) < 0)
            return 2;
        if(!strcmp(in_name, "bus")){
            c.in_mode = UART_IN_BUS;
            pin_parse("uio0", &c.strobe);
            pin_parse("uo1", &c.busy);
        }else{
            c.in_mode = UART_IN_LINE;
            if(parse_pin(in_name, &c.rx) < 0)
                return 2;
        }
        uart = uart_pty_new(&c);
        if(harness_add(&h, uart) < 0)
            return 1;
    }
    if(tap_if){
        EthCfg c;
        memset(&c, 0, sizeof(c));
        c.ifname = tap_if;
        c.pcap_path = pcap_path;
        pin_parse("ui1", &c.rd);
        pin_parse("uo1", &c.td);
        if(harness_add(&h, eth_tap_new(&c)) < 0)
            return 1;
    }
    Adapter* sp = NULL;
    if(serprog_port >= 0){
        sp = serprog_new(serprog_port);
        if(harness_add(&h, sp) < 0)
            return 1;
    }
    Adapter* rbb = NULL;
    static WiredAnd swdio, canbus;
    if(rbb_port >= 0){
        RbbCfg c;
        PinRef j[4], s[2];
        memset(&c, 0, sizeof(c));
        if(parse_pins(jtag_pins, j, 4) < 0 || parse_pins(swd_pins, s, 2) < 0)
            return 2;
        c.port = rbb_port;
        c.settle = rbb_settle;
        c.tck = j[0];
        c.tms = j[1];
        c.tdi = j[2];
        c.tdo = j[3];
        c.swclk = s[0];
        wired_and_init(&swdio, "swdio", s[1], s[1]);
        c.swdio = &swdio;
        rbb = rbb_new(&c);
        if(harness_wire(&h, &swdio) < 0 || harness_add(&h, rbb) < 0)
            return 1;
    }
    Adapter* can[2] = {NULL, NULL};
    if(n_can){
        static const char* names[2] = {"A", "B"};
        PinRef cp[2];
        if(parse_pins(can_pins, cp, 2) < 0)
            return 2;
        wired_and_init(&canbus, "can", cp[0], cp[1]);
        if(harness_wire(&h, &canbus) < 0)
            return 1;
        for(int i = 0; i < n_can; i++){
            can[i] = can_node_new(names[i], can_host[i], &canbus);
            if(harness_add(&h, can[i]) < 0)
                return 1;
        }
    }

    harness_reset(&h, 8);
    double t0 = now_s();

    int rc = 0;
    if(pin_srv){
        // stdout carries the pin protocol, so adapter addresses go to stderr
        announce(stderr, uart, sp, rbb, can);
        rc = pin_server(&h);
        fprintf(stderr, "tse_sim: top %s, %llu cycles served\n", TOP_NAME, (unsigned long long)h.cycle);
    }else if(bench){
        double c0 = cpu_s();
        for(uint64_t i = 0; i < bench; i++)
            harness_step(&h, 1);
        double dt = now_s() - t0, dc = cpu_s() - c0;
        printf("bench: top %s, %llu cycles in %.3f s = %.0f cycles/s (%.2fx real time)\n",
               TOP_NAME, (unsigned long long)bench, dt, bench / dt, bench / dt / TSE_HZ);
        // one line for scripts and nightly logs; cpu_cps is steadier on a loaded machine
        printf("BENCH top=%s cycles=%llu wall_s=%.3f cpu_s=%.3f cps=%.0f cpu_cps=%.0f\n",
               TOP_NAME, (unsigned long long)bench, dt, dc, bench / dt, bench / dc);
    }else{
        announce(stdout, uart, sp, rbb, can);
        harness_run(&h, &g_stop);
        double dt = now_s() - t0;
        fprintf(stderr, "tse_sim: top %s, %llu cycles (%.3f ms of chip time) in %.2f s wall\n",
                TOP_NAME, (unsigned long long)h.cycle, h.cycle * 1e3 / TSE_HZ, dt);
        if(uart){
            unsigned long in, out, bad;
            uart_pty_stats(uart, &in, &out, &bad);
            fprintf(stderr, "tse_sim: uart %lu bytes in, %lu out, %lu framing errors\n",
                    in, out, bad);
        }
        if(rbb){
            RbbStats s;
            rbb_stats(rbb, &s);
            fprintf(stderr, "tse_sim: rbb %lu ops, %lu reads, %lu resets, %lu SWDIO clashes\n",
                    s.ops, s.reads, s.resets, s.clashes);
        }
    }
    harness_teardown(&h);
    return rc;
}
