#ifndef TSE_VCD_PINS_H
#define TSE_VCD_PINS_H

// Pins-only VCD writer: the evidence file an outside decoder reads.
//
// Only the Tiny Tapeout pins are written, one 1-bit wire per pin
// (ui_in0..7, uo_out0..7, uio_in0..7, uio_out0..7, uio_oe0..7,
// rst_n), because that is all a logic analyser on the board could
// see and because sigrok's VCD input handles 1-bit wires best (it
// reads x as 0 and has no tristate). Values are written only when
// they change, at a 1 ns timescale, so a long idle stretch costs
// nothing. Long runs need `sigrok-cli -I vcd:downsample=N`.

#include <stdint.h>
#include <stdio.h>
#include "adapter.h"

typedef struct VcdPins {
    FILE* f;
    Pins  prev;
    int   started;
} VcdPins;

int  vcd_pins_open(VcdPins* v, const char* path);
void vcd_pins_put(VcdPins* v, const Pins* p, uint64_t cycle);
void vcd_pins_close(VcdPins* v);

#endif
