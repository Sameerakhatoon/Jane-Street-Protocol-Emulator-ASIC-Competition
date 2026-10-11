#ifndef TSE_UART_PTY_H
#define TSE_UART_PTY_H

// UART bridge: a pseudo-terminal on the host side, 8N1 frames on
// chip pins on the other. Any serial program (picocom, screen,
// a test script) opens the printed /dev/pts/N path as if it were
// a USB serial cable.
//
//   chip -> host  watch cfg.tx for a falling edge (start bit),
//                 sample each bit at its middle, write the byte to
//                 the pty. A missing stop bit is a framing error:
//                 the byte is dropped and counted, never repaired.
//   host -> chip  bytes read from the pty go into a ring, then
//                 either
//                   UART_IN_LINE: serialized 8N1 onto cfg.rx, or
//                   UART_IN_BUS:  put on ui_in[7:0], then a pulse
//                                 on cfg.strobe, then wait for
//                                 cfg.busy to fall (the UART test
//                                 transmitter top, which has no RX)
//
// Bit edges are computed exactly from model time: bit k of a frame
// that starts at cycle c0 begins at c0 + k * TSE_HZ / baud, so
// 115200 baud (520.83 cycles per bit) does not drift over a long
// frame. The adapter stays busy for 20 bit times after the last
// activity, so a reply that starts late is not frozen.

#include "adapter.h"

typedef enum UartIn {
    UART_IN_NONE = 0,
    UART_IN_LINE,
    UART_IN_BUS,
} UartIn;

typedef struct UartCfg {
    uint32_t baud;
    PinRef   tx;        // chip TX output we decode
    int      in_mode;   // UartIn
    PinRef   rx;        // LINE: chip RX input we drive
    PinRef   strobe;    // BUS : rising edge sends ui_in
    PinRef   busy;      // BUS : high while the chip transmits
} UartCfg;

Adapter*    uart_pty_new(const UartCfg* cfg);
const char* uart_pty_path(Adapter* a);
void        uart_pty_stats(Adapter* a, unsigned long* in, unsigned long* out,
                           unsigned long* bad);

#endif
