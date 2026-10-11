#include <string.h>
#include "vcd_pins.h"

// Five 8-bit buses then rst_n. VCD ids are one printable char each,
// starting at '!' (41 wires, '!'..'I').
static const char* BUS_NAME[5] = {"ui_in", "uo_out", "uio_in", "uio_out", "uio_oe"};

static uint8_t bus_val(const Pins* p, int bus){
    switch(bus){
    case 0: return p->ui_in;
    case 1: return p->uo_out;
    case 2: return p->uio_in;
    case 3: return p->uio_out;
    case 4: return p->uio_oe;
    }
    return 0;
}

int vcd_pins_open(VcdPins* v, const char* path){
    memset(v, 0, sizeof(*v));
    v->f = fopen(path, "w");
    if(!v->f){
        perror(path);
        return -1;
    }
    fprintf(v->f, "$timescale 1ns $end\n$scope module tse $end\n");
    for(int b = 0; b < 5; b++)
        for(int i = 0; i < 8; i++)
            fprintf(v->f, "$var wire 1 %c %s%d $end\n", '!' + b * 8 + i, BUS_NAME[b], i);
    fprintf(v->f, "$var wire 1 %c rst_n $end\n", '!' + 40);
    fprintf(v->f, "$upscope $end\n$enddefinitions $end\n");
    return 0;
}

void vcd_pins_put(VcdPins* v, const Pins* p, uint64_t cycle){
    if(!v->f)
        return;
    int stamped = 0;
    for(int b = 0; b < 5; b++){
        uint8_t now = bus_val(p, b), was = bus_val(&v->prev, b);
        uint8_t diff = v->started ? (uint8_t)(now ^ was) : 0xff;
        for(int i = 0; diff && i < 8; i++){
            if(!((diff >> i) & 1))
                continue;
            if(!stamped++)
                fprintf(v->f, "#%llu\n", (unsigned long long)(cycle * 50 / 3));
            fprintf(v->f, "%d%c\n", (now >> i) & 1, '!' + b * 8 + i);
        }
    }
    if(!v->started || p->rst_n != v->prev.rst_n){
        if(!stamped++)
            fprintf(v->f, "#%llu\n", (unsigned long long)(cycle * 50 / 3));
        fprintf(v->f, "%d%c\n", p->rst_n & 1, '!' + 40);
    }
    v->prev = *p;
    v->started = 1;
}

void vcd_pins_close(VcdPins* v){
    if(v->f)
        fclose(v->f);
    v->f = NULL;
}
