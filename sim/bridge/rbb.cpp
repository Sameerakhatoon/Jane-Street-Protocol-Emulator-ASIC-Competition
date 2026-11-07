// SPDX-License-Identifier: Apache-2.0
#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
#include "rbb.h"

#define IN_BUF      16384
#define OUT_BUF     4096

typedef struct Rbb {
    RbbCfg   c;
    int      lfd, cfd;
    char     in[IN_BUF];
    int      nin, pos;
    char     out[OUT_BUF];
    int      nout;
    int      wait;
    int      sw_id;             // our driver slot on the SWDIO bus
    int      sw_drive, sw_val;
    int      quit;
    RbbStats st;
} Rbb;

static void flush_out(Rbb* r){
    if(r->nout && r->cfd >= 0 && write(r->cfd, r->out, r->nout) != r->nout)
        perror("rbb: write");
    r->nout = 0;
}

static void reply(Rbb* r, int bit){
    if(r->nout == OUT_BUF)
        flush_out(r);
    r->out[r->nout++] = bit ? '1' : '0';
    r->st.reads++;
}

static void sw_apply(Rbb* r){
    if(r->c.swdio)
        wired_and_drive_dir(r->c.swdio, r->sw_id, r->sw_drive, r->sw_val);
}

static void hangup(Adapter* a, Rbb* r){
    if(r->cfd >= 0)
        close(r->cfd);
    r->cfd = -1;
    r->nin = r->pos = r->nout = r->wait = r->quit = 0;
    r->sw_drive = 0;
    sw_apply(r);
    a->fd = r->lfd;                     // wait for the next OpenOCD
}

static int rbb_init(Adapter* a, Pins* p){
    Rbb* r = (Rbb*)a->ctx;
    pin_set(p, r->c.tck, 0);
    pin_set(p, r->c.tms, 1);
    pin_set(p, r->c.tdi, 0);
    pin_set(p, r->c.swclk, 0);
    if(r->c.swdio){
        r->sw_id = wired_and_attach(r->c.swdio);
        if(r->sw_id < 0)
            return -1;
    }
    r->lfd = socket(AF_INET, SOCK_STREAM, 0);
    int one = 1;
    setsockopt(r->lfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in sa;
    memset(&sa, 0, sizeof(sa));
    sa.sin_family = AF_INET;
    sa.sin_port = htons((uint16_t)r->c.port);
    sa.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if(bind(r->lfd, (struct sockaddr*)&sa, sizeof(sa)) < 0 || listen(r->lfd, 1) < 0){
        perror("rbb: bind");
        close(r->lfd);
        return -1;
    }
    socklen_t len = sizeof(sa);
    getsockname(r->lfd, (struct sockaddr*)&sa, &len);
    r->c.port = ntohs(sa.sin_port);
    a->fd = r->lfd;
    return 0;
}

static int rbb_poll(Adapter* a, short revents){
    Rbb* r = (Rbb*)a->ctx;
    int busy = r->pos < r->nin || r->wait > 0;
    if(!(revents & (POLLIN | POLLHUP | POLLERR)))
        return busy;
    if(r->cfd < 0){
        r->cfd = accept(r->lfd, NULL, NULL);
        if(r->cfd >= 0){
            int one = 1;
            setsockopt(r->cfd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
            a->fd = r->cfd;
            // OpenOCD sends the line reset and JTAG-to-SWD bits before its
            // first O, so a fresh connection starts with the host driving.
            r->sw_drive = 1;
            r->sw_val = 1;
            sw_apply(r);
        }
        return busy;
    }
    if(r->pos){
        memmove(r->in, r->in + r->pos, r->nin - r->pos);
        r->nin -= r->pos;
        r->pos = 0;
    }
    if(r->nin == IN_BUF)
        return 1;                       // full: run time until we have taken some
    ssize_t n = read(r->cfd, r->in + r->nin, IN_BUF - r->nin);
    if(n <= 0){
        hangup(a, r);
        return 0;
    }
    r->nin += (int)n;
    return 1;
}

static void rbb_tick(Adapter* a, Pins* p, uint64_t cycle){
    Rbb* r = (Rbb*)a->ctx;
    (void)cycle;
    if(r->c.swdio && r->sw_drive && wired_and_chip_low(r->c.swdio, p) && r->sw_val)
        r->st.clashes++;
    if(r->wait > 0){
        r->wait--;
        return;
    }
    while(r->pos < r->nin && !r->wait){
        char ch = r->in[r->pos++];
        r->st.ops++;
        if(ch >= '0' && ch <= '7'){
            int v = ch - '0';
            pin_set(p, r->c.tck, (v >> 2) & 1);
            pin_set(p, r->c.tms, (v >> 1) & 1);
            pin_set(p, r->c.tdi, v & 1);
            r->wait = r->c.settle;
        }else if(ch >= 'd' && ch <= 'g'){
            int v = ch - 'd';
            pin_set(p, r->c.swclk, (v >> 1) & 1);
            r->sw_val = v & 1;
            sw_apply(r);
            r->wait = r->c.settle;
        }else if(ch == 'R'){
            reply(r, pin_get(p, r->c.tdo));
        }else if(ch == 'c'){
            reply(r, r->c.swdio ? wired_and_level(r->c.swdio) : 1);
        }else if(ch == 'O' || ch == 'o'){
            r->sw_drive = ch == 'O';
            sw_apply(r);
        }else if(ch >= 'r' && ch <= 'u'){
            r->st.resets++;
        }else if(ch == 'Q'){
            r->quit = 1;
        }
    }
    if(r->wait)
        r->wait--;                      // this clock is the first of the hold
    if(r->pos == r->nin && !r->wait){
        if(r->nout){
            flush_out(r);
            a->yield = 1;               // OpenOCD now thinks; stop chip time
        }
        if(r->quit)
            hangup(a, r);
    }
}

static void rbb_teardown(Adapter* a){
    Rbb* r = (Rbb*)a->ctx;
    if(r->cfd >= 0)
        close(r->cfd);
    if(r->lfd >= 0)
        close(r->lfd);
    free(r);
    free(a);
}

Adapter* rbb_new(const RbbCfg* c){
    Adapter* a = (Adapter*)calloc(1, sizeof(Adapter));
    Rbb*     r = (Rbb*)calloc(1, sizeof(Rbb));
    r->c = *c;
    if(r->c.settle < 1)
        r->c.settle = 1;
    r->lfd = -1;
    r->cfd = -1;
    a->name = "remote_bitbang";
    a->ctx = r;
    a->fd = -1;
    a->init = rbb_init;
    a->poll = rbb_poll;
    a->tick = rbb_tick;
    a->teardown = rbb_teardown;
    return a;
}

int rbb_port_of(Adapter* a){
    return ((Rbb*)a->ctx)->c.port;
}

void rbb_stats(Adapter* a, RbbStats* s){
    *s = ((Rbb*)a->ctx)->st;
}
