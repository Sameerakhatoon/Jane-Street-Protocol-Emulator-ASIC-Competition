// SPDX-License-Identifier: Apache-2.0
#ifndef TSE_WIRED_AND_H
#define TSE_WIRED_AND_H

// Open-drain bus resolver for I2C, 1-Wire, SWDIO and the CAN bus model.
//
// Every driver either pulls the line low or lets go; a pull-up makes it
// high when nobody pulls. Host-side models attach as drivers and call
// wired_and_drive() from their tick. The chip may be one more driver:
//
//   chip_out on a uio pin  pulls low when uio_oe = 1 and uio_out = 0
//                          (push-pull high counts as letting go)
//   chip_out on a uo pin   pulls low when the pin is 0 (a CAN TX pin)
//   chip_in                the pin that receives the resolved level
//                          (ui or uio), PIN_NONE for a host-only bus
//
// The harness resolves every registered bus after all adapter ticks and
// before the inputs go into the model, so a driver sees its own change
// one cycle later, like any synchronous receiver.
//
// Each resolve also records who drives the line (host_on, chip_on), so
// the pins-only VCD can show the wire and its owner the way a logic
// analyser with a direction probe would. An open-drain driver counts as
// driving while it pulls low; a host with a direction (a debug probe on
// SWDIO) uses wired_and_drive_dir and counts while its output is on.

#include "adapter.h"

#define WIRED_MAX_DRIVERS   8

typedef struct WiredAnd {
    const char* name;
    PinRef      chip_out;
    PinRef      chip_in;
    int         n;
    uint8_t     low[WIRED_MAX_DRIVERS];
    uint8_t     on[WIRED_MAX_DRIVERS];
    uint8_t     level;
    uint8_t     host_on;            // a host driver drove at the last resolve
    uint8_t     chip_on;            // the chip drove at the last resolve
    uint64_t    falls;              // falling edges seen, for tests and stats
} WiredAnd;

void wired_and_init(WiredAnd* w, const char* name, PinRef chip_out, PinRef chip_in);
int  wired_and_attach(WiredAnd* w);                 // driver id, or -1 when full
int  wired_and_chip_low(const WiredAnd* w, const Pins* p);
int  wired_and_resolve(WiredAnd* w, Pins* p);       // new level, also put on chip_in

static inline void wired_and_drive(WiredAnd* w, int id, int v){
    w->low[id] = v ? 0 : 1;
    w->on[id] = w->low[id];
}

// A driver with a direction: on = 0 lets go, on = 1 drives v (a driven 1
// pulls nothing, the pull-up gives the same level).
static inline void wired_and_drive_dir(WiredAnd* w, int id, int on, int v){
    w->low[id] = (uint8_t)(on && !v);
    w->on[id] = (uint8_t)(on != 0);
}

static inline int wired_and_level(const WiredAnd* w){
    return w->level;
}

#endif
