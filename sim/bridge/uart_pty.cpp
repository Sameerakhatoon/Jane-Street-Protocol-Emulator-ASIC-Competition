// SPDX-License-Identifier: Apache-2.0
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <unistd.h>
#include "uart_pty.h"

#define UART_RING   4096        // power of two

enum { B_IDLE, B_SETUP, B_STROBE, B_WAIT_HI, B_WAIT_LO };

typedef struct UartPty {
    UartCfg  cfg;
    int      sfd;               // slave kept open: no EIO/HUP while no client
    char     path[64];
    uint8_t  ring[UART_RING];
    unsigned head, tail;        // ring: host -> chip
    uint64_t now;
    uint64_t linger_until;
    int      drive;             // LINE: a frame is on cfg.rx
    uint16_t frame;             // {stop, data, start}, LSB first
    uint64_t d_start;
    int      bstate;            // BUS feeder state
    uint64_t b_until;
    int      sample;            // a frame is arriving on cfg.tx
    uint64_t s_start;
    int      s_bit;
    uint8_t  sh;
    int      prev_tx;
    unsigned long n_in, n_out, n_bad;
} UartPty;

static uint64_t bit_edge(const UartPty* u, uint64_t start, uint64_t halfbits){
    return start + halfbits * TSE_HZ / (2ull * u->cfg.baud);
}

static int ring_empty(const UartPty* u){
    return u->head == u->tail;
}

static uint8_t ring_pop(UartPty* u){
    return u->ring[u->tail++ & (UART_RING - 1)];
}

static void own_pin(Adapter* a, PinRef r){
    if(r.bus == PIN_UI)
        a->own_ui |= (uint8_t)(1u << r.bit);
    if(r.bus == PIN_UIO)
        a->own_uio |= (uint8_t)(1u << r.bit);
}

static int uart_init(Adapter* a, Pins* p){
    UartPty* u = (UartPty*)a->ctx;
    int mfd = posix_openpt(O_RDWR | O_NOCTTY);
    if(mfd < 0 || grantpt(mfd) < 0 || unlockpt(mfd) < 0){
        perror("uart: posix_openpt");
        return -1;
    }
    snprintf(u->path, sizeof(u->path), "%s", ptsname(mfd));
    u->sfd = open(u->path, O_RDWR | O_NOCTTY);
    if(u->sfd < 0){
        perror("uart: open slave");
        close(mfd);
        return -1;
    }
    struct termios t;
    tcgetattr(u->sfd, &t);
    cfmakeraw(&t);
    tcsetattr(u->sfd, TCSANOW, &t);
    fcntl(mfd, F_SETFL, O_NONBLOCK);
    a->fd = mfd;
    u->prev_tx = 1;
    if(u->cfg.in_mode == UART_IN_LINE){
        pin_set(p, u->cfg.rx, 1);
        own_pin(a, u->cfg.rx);
    }
    if(u->cfg.in_mode == UART_IN_BUS){
        pin_set(p, u->cfg.strobe, 0);
        a->own_ui = 0xff;
        own_pin(a, u->cfg.strobe);
    }
    return 0;
}

static int uart_poll(Adapter* a, short revents){
    UartPty* u = (UartPty*)a->ctx;
    if(revents & POLLIN){
        unsigned room = UART_RING - (u->head - u->tail);
        uint8_t  b[256];
        if(room > sizeof(b))
            room = sizeof(b);
        ssize_t n = room ? read(a->fd, b, room) : 0;
        for(ssize_t i = 0; i < n; i++)
            u->ring[u->head++ & (UART_RING - 1)] = b[i];
    }
    return !ring_empty(u) || u->drive || u->bstate != B_IDLE || u->sample
        || u->now < u->linger_until;
}

static void drive_line(UartPty* u, Pins* p){
    if(!u->drive && !ring_empty(u)){
        u->frame = (uint16_t)((1u << 9) | (ring_pop(u) << 1));
        u->d_start = u->now;
        u->drive = 1;
        u->n_in++;
    }
    if(!u->drive)
        return;
    uint64_t k = (u->now - u->d_start) * u->cfg.baud / TSE_HZ;
    if(k >= 10){
        u->drive = 0;
        pin_set(p, u->cfg.rx, 1);
        return;
    }
    pin_set(p, u->cfg.rx, (u->frame >> k) & 1);
}

static void drive_bus(UartPty* u, Pins* p){
    switch(u->bstate){
    case B_IDLE:
        if(ring_empty(u))
            return;
        p->ui_in = ring_pop(u);
        u->n_in++;
        u->b_until = u->now + 4;            // data settles through the input synchronizer
        u->bstate = B_SETUP;
        break;
    case B_SETUP:
        if(u->now < u->b_until)
            return;
        pin_set(p, u->cfg.strobe, 1);
        u->b_until = u->now + 4;
        u->bstate = B_STROBE;
        break;
    case B_STROBE:
        if(u->now < u->b_until)
            return;
        pin_set(p, u->cfg.strobe, 0);
        u->b_until = u->now + 256;
        u->bstate = B_WAIT_HI;
        break;
    case B_WAIT_HI:
        if(pin_get(p, u->cfg.busy) || u->now >= u->b_until)
            u->bstate = B_WAIT_LO;
        break;
    case B_WAIT_LO:
        if(!pin_get(p, u->cfg.busy))
            u->bstate = B_IDLE;
        break;
    }
}

static void sample_tx(Adapter* a, UartPty* u, const Pins* p){
    int tx = pin_get(p, u->cfg.tx);
    if(!u->sample){
        if(u->prev_tx && !tx){
            u->sample = 1;
            u->s_start = u->now;
            u->s_bit = 0;
            u->sh = 0;
        }
    }else if(u->now >= bit_edge(u, u->s_start, 2 * u->s_bit + 1)){
        if(u->s_bit == 0 && tx){
            u->sample = 0;                  // glitch, not a start bit
        }else if(u->s_bit >= 1 && u->s_bit <= 8){
            u->sh |= (uint8_t)(tx << (u->s_bit - 1));
        }else if(u->s_bit == 9){
            u->sample = 0;
            if(!tx){
                u->n_bad++;
                fprintf(stderr, "uart: framing error, byte %02x dropped\n", u->sh);
            }else if(write(a->fd, &u->sh, 1) == 1){
                u->n_out++;
            }
        }
        u->s_bit++;
    }
    u->prev_tx = tx;
}

static void uart_tick(Adapter* a, Pins* p, uint64_t cycle){
    UartPty* u = (UartPty*)a->ctx;
    u->now = cycle;
    if(u->cfg.in_mode == UART_IN_LINE)
        drive_line(u, p);
    else if(u->cfg.in_mode == UART_IN_BUS)
        drive_bus(u, p);
    sample_tx(a, u, p);
    if(u->drive || u->sample || u->bstate != B_IDLE)
        u->linger_until = cycle + 20 * TSE_HZ / u->cfg.baud;
}

static void uart_teardown(Adapter* a){
    UartPty* u = (UartPty*)a->ctx;
    if(a->fd >= 0)
        close(a->fd);
    if(u->sfd >= 0)
        close(u->sfd);
    free(u);
    free(a);
}

Adapter* uart_pty_new(const UartCfg* cfg){
    Adapter* a = (Adapter*)calloc(1, sizeof(Adapter));
    UartPty* u = (UartPty*)calloc(1, sizeof(UartPty));
    u->cfg = *cfg;
    u->sfd = -1;
    a->name = "uart";
    a->ctx = u;
    a->fd = -1;
    a->init = uart_init;
    a->poll = uart_poll;
    a->tick = uart_tick;
    a->teardown = uart_teardown;
    return a;
}

const char* uart_pty_path(Adapter* a){
    return ((UartPty*)a->ctx)->path;
}

void uart_pty_stats(Adapter* a, unsigned long* in, unsigned long* out, unsigned long* bad){
    UartPty* u = (UartPty*)a->ctx;
    *in = u->n_in;
    *out = u->n_out;
    *bad = u->n_bad;
}
