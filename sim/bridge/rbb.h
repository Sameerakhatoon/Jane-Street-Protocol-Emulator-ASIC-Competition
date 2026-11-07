// SPDX-License-Identifier: Apache-2.0
#ifndef TSE_RBB_H
#define TSE_RBB_H

// OpenOCD remote_bitbang server: real OpenOCD drives JTAG or SWD pins of
// the model over TCP, one ASCII character per pin operation.
//
//   openocd -c "adapter driver remote_bitbang; remote_bitbang port PORT" ...
//
//   '0'..'7'  JTAG write: value = TCK * 4 + TMS * 2 + TDI
//   'R'       reply '0' or '1' with TDO
//   'd'..'g'  SWD write: value - 'd' = SWCLK * 2 + SWDIO
//   'O' 'o'   host drives SWDIO / host lets go of SWDIO
//   'c'       reply '0' or '1' with the SWDIO line
//   'r'..'u'  reset lines (TRST, SRST): counted, not wired
//   'B' 'b'   LED: ignored      'Q' quit: close the connection
//
// OpenOCD waits for the answer to every 'R' and 'c', so the model runs in
// lockstep with it and there is no timeout to meet. Each write holds the
// pins for `settle` clocks before the next character is taken, so a target
// that samples TCK with the 60 MHz clock sees clean edges: settle 4 gives
// TCK = 60 MHz / 8 = 7.5 MHz. After a reply the adapter yields, so chip
// time stops until OpenOCD sends more.
//
// SWDIO sits on a WiredAnd bus: the host side is one driver (low while it
// drives a 0), the chip is the other (uio_oe and uio_out of that pin).

#include "adapter.h"
#include "wired_and.h"

typedef struct RbbCfg {
    int       port;         // 0 = pick a free port
    int       settle;       // clocks per pin write
    PinRef    tck, tms, tdi, tdo;
    PinRef    swclk;
    WiredAnd* swdio;        // NULL: no SWD
} RbbCfg;

typedef struct RbbStats {
    unsigned long ops;      // characters taken
    unsigned long reads;    // 'R' and 'c' answered
    unsigned long resets;   // 'r'..'u'
    unsigned long clashes;  // host drove SWDIO while the chip did too
} RbbStats;

Adapter* rbb_new(const RbbCfg* c);
int      rbb_port_of(Adapter* a);
void     rbb_stats(Adapter* a, RbbStats* s);

#endif
