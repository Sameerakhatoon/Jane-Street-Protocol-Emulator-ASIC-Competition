#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>
#include "Vtop.h"
#include "verilated.h"
#ifndef TSE_NO_TRACE
#include "verilated_fst_c.h"
#endif
#include "harness.h"

// Model time in ps for the FST file. 60 MHz is 16666.67 ps per cycle,
// so use the exact fraction and let the integer division round.
static uint64_t cycle_ps(uint64_t c){
    return c * 50000 / 3;
}

int harness_init(Harness* h, int argc, char** argv){
    memset(h, 0, sizeof(*h));
    h->vctx = new VerilatedContext;
    h->vctx->commandArgs(argc, argv);
    h->vctx->traceEverOn(true);
    h->top = new Vtop{h->vctx};
    h->pins.ena = 1;
    h->pins.rst_n = 1;
    h->quantum = 4096;
    h->idle_ms = 50;
    return 0;
}

int harness_trace(Harness* h, const char* path, uint64_t from, uint64_t cycles){
#ifdef TSE_NO_TRACE
    (void)h; (void)from; (void)cycles;
    fprintf(stderr, "harness: built with TRACE=0, cannot write %s\n", path);
    return -1;
#else
    h->fst = new VerilatedFstC;
    h->top->trace(h->fst, 99);
    h->fst->open(path);
    if(!h->fst->isOpen()){
        fprintf(stderr, "harness: cannot open %s\n", path);
        delete h->fst;
        h->fst = NULL;
        return -1;
    }
    h->fst_from = from;
    h->fst_until = cycles ? from + cycles : UINT64_MAX;
    return 0;
#endif
}

int harness_vcd(Harness* h, const char* path){
    return vcd_pins_open(&h->vcd, path);
}

int harness_add(Harness* h, Adapter* a){
    if(h->n_ad == HARNESS_MAX_ADAPTERS){
        fprintf(stderr, "harness: too many adapters\n");
        return -1;
    }
    if(a->init && a->init(a, &h->pins) < 0){
        fprintf(stderr, "harness: adapter %s failed to start\n", a->name);
        return -1;
    }
    h->ad[h->n_ad++] = a;
    return 0;
}

#ifdef TSE_NO_TRACE
static void fst_dump(Harness* h, uint64_t t){
    (void)h;
    (void)t;
}
#else
static void fst_dump(Harness* h, uint64_t t){
    h->fst->dump(t);
}
#endif

static void push_inputs(Harness* h){
    h->top->ui_in  = h->pins.ui_in;
    h->top->uio_in = h->pins.uio_in;
    h->top->rst_n  = h->pins.rst_n;
    h->top->ena    = h->pins.ena;
}

static void pull_outputs(Harness* h){
    h->pins.uo_out  = h->top->uo_out;
    h->pins.uio_out = h->top->uio_out;
    h->pins.uio_oe  = h->top->uio_oe;
}

int harness_wire(Harness* h, WiredAnd* w){
    if(h->n_wire == HARNESS_MAX_WIRES){
        fprintf(stderr, "harness: too many wired buses\n");
        return -1;
    }
    h->wire[h->n_wire++] = w;
    return 0;
}

void harness_step(Harness* h, int tick_adapters){
    if(tick_adapters)
        for(int i = 0; i < h->n_ad; i++)
            h->ad[i]->tick(h->ad[i], &h->pins, h->cycle);
    for(int i = 0; i < h->n_wire; i++)
        wired_and_resolve(h->wire[i], &h->pins);
    int dump = h->fst && h->cycle >= h->fst_from && h->cycle < h->fst_until;
    uint64_t t = cycle_ps(h->cycle);
    push_inputs(h);
    h->top->clk = 0;
    h->top->eval();
    if(dump)
        fst_dump(h, t);
    h->top->clk = 1;
    h->top->eval();
    if(dump)
        fst_dump(h, t + (cycle_ps(h->cycle + 1) - t) / 2);
    pull_outputs(h);
    if(h->vcd.f)
        vcd_pins_put(&h->vcd, &h->pins, h->cycle);
    h->cycle++;
}

void harness_reset(Harness* h, int cycles){
    h->pins.rst_n = 0;
    for(int i = 0; i < cycles; i++)
        harness_step(h, 0);
    h->pins.rst_n = 1;
}

void harness_run(Harness* h, volatile int* stop){
    struct pollfd pfd[HARNESS_MAX_ADAPTERS];
    int           who[HARNESS_MAX_ADAPTERS];
    int           busy = 0;

    while(!*stop && (!h->stop_at || h->cycle < h->stop_at)){
        int n = 0;
        for(int i = 0; i < h->n_ad; i++){
            if(h->ad[i]->fd < 0)
                continue;
            pfd[n].fd = h->ad[i]->fd;
            pfd[n].events = POLLIN;
            pfd[n].revents = 0;
            who[n++] = i;
        }
        if(poll(pfd, n, busy ? 0 : h->idle_ms) < 0 && errno != EINTR){
            perror("harness: poll");
            return;
        }
        busy = h->free_run;
        for(int i = 0; i < h->n_ad; i++){
            short rev = 0;
            for(int k = 0; k < n; k++)
                if(who[k] == i)
                    rev = pfd[k].revents;
            busy |= h->ad[i]->poll(h->ad[i], rev);
        }
        int yield = 0;
        for(int q = 0; busy && !yield && q < h->quantum; q++){
            harness_step(h, 1);
            for(int i = 0; i < h->n_ad; i++){
                yield |= h->ad[i]->yield;
                h->ad[i]->yield = 0;
            }
        }
    }
}

void harness_teardown(Harness* h){
    for(int i = h->n_ad - 1; i >= 0; i--)
        if(h->ad[i]->teardown)
            h->ad[i]->teardown(h->ad[i]);
    h->n_ad = 0;
#ifndef TSE_NO_TRACE
    if(h->fst){
        h->fst->close();
        delete h->fst;
        h->fst = NULL;
    }
#endif
    vcd_pins_close(&h->vcd);
    h->top->final();
    delete h->top;
    delete h->vctx;
}
