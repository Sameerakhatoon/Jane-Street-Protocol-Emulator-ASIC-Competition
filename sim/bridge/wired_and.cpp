#include <string.h>
#include "wired_and.h"

void wired_and_init(WiredAnd* w, const char* name, PinRef chip_out, PinRef chip_in){
    memset(w, 0, sizeof(*w));
    w->name = name;
    w->chip_out = chip_out;
    w->chip_in = chip_in;
    w->level = 1;
}

int wired_and_attach(WiredAnd* w){
    if(w->n == WIRED_MAX_DRIVERS)
        return -1;
    w->low[w->n] = 0;
    return w->n++;
}

int wired_and_chip_low(const WiredAnd* w, const Pins* p){
    uint8_t m = (uint8_t)(1u << w->chip_out.bit);
    switch(w->chip_out.bus){
    case PIN_UIO: return (p->uio_oe & m) && !(p->uio_out & m);
    case PIN_UO:  return !(p->uo_out & m);
    }
    return 0;
}

int wired_and_resolve(WiredAnd* w, Pins* p){
    int low = wired_and_chip_low(w, p);
    for(int i = 0; i < w->n && !low; i++)
        low = w->low[i];
    if(low && w->level)
        w->falls++;
    w->level = low ? 0 : 1;
    pin_set(p, w->chip_in, w->level);
    return w->level;
}
