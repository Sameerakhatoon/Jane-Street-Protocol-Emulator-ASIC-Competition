#ifndef TSE_HARNESS_H
#define TSE_HARNESS_H

// The clock owner. One process, one thread, one Verilated chip.
//
//   loop: poll(all adapter fds, timeout 0 if any adapter is busy,
//              else idle_ms)
//         adapter->poll(revents)      host side, non-blocking
//         if any busy: run `quantum` clock cycles
//   step: adapter->tick() for each, push input pins, clk 0 eval,
//         clk 1 eval, pull output pins, optional FST dump
//
// Simulated time moves only while some adapter is busy, so a demo
// that waits for a human costs no CPU and the chip sees no idle
// gaps it did not ask for. Time is counted in cycles of the 60 MHz
// model clock (TSE_HZ); cycle N starts at N * 16.667 ns.
//
// The Verilated top is always built with --prefix Vtop, so this
// file does not care which tt_um_* module is underneath.
//
// free_run is for a chip that talks first (a TX-only program printing
// a banner): no adapter is busy, yet the chip needs time to run.
//
// An adapter that has just answered its host and now waits for the next
// request (remote_bitbang 'R') sets a->yield; the harness ends the quantum
// there, so a lockstep host costs one poll turn per round trip instead of
// a whole quantum of chip time.
//
// Open-drain buses (WiredAnd) registered with harness_wire are resolved
// once per step, after the adapter ticks and before the model inputs.
//
// A uio pad the chip drives reads back its own level on uio_in, as the
// Tiny Tapeout pad does, so one lane can listen to a pin another lane
// drives and the VCD shows the pad level on uio_in.
//
// vcd_hold keeps the pins-only VCD closed to samples until it is cleared
// (the pin server's V line), so a trace can start when the lanes start.

#include <stdint.h>
#include "adapter.h"
#include "vcd_pins.h"
#include "wired_and.h"

#define HARNESS_MAX_ADAPTERS    8
#define HARNESS_MAX_WIRES       8

class Vtop;
class VerilatedContext;
class VerilatedFstC;

typedef struct Harness {
    VerilatedContext* vctx;
    Vtop*             top;
    VerilatedFstC*    fst;          // NULL when tracing is off
    uint64_t          fst_from;     // first cycle to dump
    uint64_t          fst_until;    // first cycle not to dump
    uint64_t          cycle;
    Pins              pins;
    VcdPins           vcd;          // pins-only VCD, f == NULL when off
    int               free_run;     // 1: time runs even when no adapter is busy
    uint64_t          stop_at;      // harness_run returns at this cycle, 0 = never
    Adapter*          ad[HARNESS_MAX_ADAPTERS];
    int               n_ad;
    WiredAnd*         wire[HARNESS_MAX_WIRES];
    int               n_wire;
    int               quantum;      // cycles per batch between poll() calls
    int               idle_ms;      // poll() timeout when nobody is busy
    int               vcd_hold;     // 1: no VCD samples yet
} Harness;

int  harness_init(Harness* h, int argc, char** argv);
int  harness_trace(Harness* h, const char* path, uint64_t from, uint64_t cycles);
int  harness_vcd(Harness* h, const char* path);
int  harness_add(Harness* h, Adapter* a);     // calls a->init
int  harness_wire(Harness* h, WiredAnd* w);
void harness_reset(Harness* h, int cycles);   // rst_n low, adapters not ticked
void harness_step(Harness* h, int tick_adapters);
int  harness_poll(Harness* h, int timeout_ms);  // poll adapter fds; 1 if one is busy
void harness_run(Harness* h, volatile int* stop);
void harness_teardown(Harness* h);

#endif
