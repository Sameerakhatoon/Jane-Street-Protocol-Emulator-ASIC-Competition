#ifndef TSE_ADAPTER_H
#define TSE_ADAPTER_H

// Pin model and adapter interface shared by every bridge.
//
// The chip is seen only through its Tiny Tapeout pins. Pins is a
// plain copy of them; the harness pushes the inputs into the
// Verilated model before each clock edge and pulls the outputs
// back after it. Adapters never include Verilator headers, so an
// adapter is ordinary code that can be built and tested alone.
//
// An adapter is one real program on the host side (a terminal on
// a pty, Linux on a TAP, flashrom on a socket). It is a struct of
// function pointers plus a context pointer:
//
//   init      set idle levels on the pins, open host fds. 0 or -1.
//   poll      called once per loop turn. revents is the poll()
//             result for a->fd (0 if none); read host data here
//             without blocking. Return 1 if the adapter needs
//             simulated time right now, 0 if it can sleep.
//   tick      once per clock while the harness runs time: drive
//             inputs, sample outputs. Never blocks.
//   teardown  close fds, free memory.
//
// Rules: one thread, adapters never call eval(), never block, never
// tick during reset, and see outputs as they were after the last
// rising edge. yield = 1 (set in tick) ends the current quantum early.
// own_ui and own_uio mark the input bits the adapter drives (set in init):
// the pin server's S line leaves them alone, so a host program and an
// adapter can share the input bytes.

#include <stdint.h>

#define TSE_HZ          60000000ull     // model clock: 60 MHz, the TT demo board default

typedef struct Pins {
    uint8_t ui_in;      // in : dedicated inputs
    uint8_t uo_out;     // out: dedicated outputs
    uint8_t uio_in;     // in : bidirectional, input path
    uint8_t uio_out;    // out: bidirectional, output path
    uint8_t uio_oe;     // out: 1 = chip drives that uio pin
    uint8_t rst_n;      // in
    uint8_t ena;        // in : always 1 on the board
} Pins;

typedef enum PinBus {
    PIN_NONE = 0,
    PIN_UI,             // ui_in[bit]
    PIN_UO,             // uo_out[bit]
    PIN_UIO,            // uio_in[bit] when driven by us, uio_out[bit] when read
} PinBus;

typedef struct PinRef {
    uint8_t bus;        // PinBus
    uint8_t bit;
} PinRef;

static inline int pin_get(const Pins* p, PinRef r){
    switch(r.bus){
    case PIN_UI:  return (p->ui_in  >> r.bit) & 1;
    case PIN_UO:  return (p->uo_out >> r.bit) & 1;
    case PIN_UIO: return ((p->uio_oe >> r.bit) & 1) ? (p->uio_out >> r.bit) & 1
                                                    : (p->uio_in  >> r.bit) & 1;
    }
    return 1;
}

static inline void pin_set(Pins* p, PinRef r, int v){
    uint8_t m = (uint8_t)(1u << r.bit);
    uint8_t* b = r.bus == PIN_UI ? &p->ui_in : r.bus == PIN_UIO ? &p->uio_in : 0;
    if(!b)
        return;
    *b = v ? (uint8_t)(*b | m) : (uint8_t)(*b & ~m);
}

// "ui3", "uo0", "uio7" -> PinRef. Returns 0, or -1 on a bad name.
int pin_parse(const char* s, PinRef* out);

// pin_parse, or a name from the pinout table ("XIO2", "RUN.SPI_MISO",
// "UART.UART_TX"). open_drain may be NULL. Returns 0, or -1 if unknown.
int pin_lookup(const char* s, PinRef* out, int* open_drain);

typedef struct Adapter Adapter;

struct Adapter {
    const char* name;
    void*       ctx;
    int         fd;         // host fd the harness polls for input, or -1
    int         yield;      // set in tick: end this quantum, the harness clears it
    uint8_t     own_ui;     // input bits this adapter drives
    uint8_t     own_uio;
    int  (*init)(Adapter* a, Pins* p);
    int  (*poll)(Adapter* a, short revents);
    void (*tick)(Adapter* a, Pins* p, uint64_t cycle);
    void (*teardown)(Adapter* a);
};

#endif
