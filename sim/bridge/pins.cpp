#include <string.h>
#include <strings.h>
#include "pins_gen.h"

int pin_parse(const char* s, PinRef* out){
    int skip;
    if(!strncmp(s, "uio", 3)){
        out->bus = PIN_UIO;
        skip = 3;
    }else if(!strncmp(s, "ui", 2)){
        out->bus = PIN_UI;
        skip = 2;
    }else if(!strncmp(s, "uo", 2)){
        out->bus = PIN_UO;
        skip = 2;
    }else{
        return -1;
    }
    if(s[skip] < '0' || s[skip] > '7' || s[skip + 1])
        return -1;
    out->bit = (uint8_t)(s[skip] - '0');
    return 0;
}

// "uio2", "XIO2" or "RUN.XIO2". A bare name is looked up in RUN, then
// LOAD, then UART, so the full chip's run-mode names win.
int pin_lookup(const char* s, PinRef* out, int* open_drain){
    static const char* order[] = {"RUN", "LOAD", "UART"};
    const char* dot = strchr(s, '.');
    if(open_drain)
        *open_drain = 0;
    if(pin_parse(s, out) == 0)
        return 0;
    for(int k = 0; k < 3; k++)
        for(int i = 0; i < PIN_NAMES_N; i++){
            const PinName* p = &PIN_NAMES[i];
            int hit = dot ? (size_t)(dot - s) == strlen(p->mode) && !strncasecmp(s, p->mode, dot - s)
                            && !strcasecmp(dot + 1, p->name)
                          : !strcmp(p->mode, order[k]) && !strcasecmp(s, p->name);
            if(hit){
                *out = p->ref;
                if(open_drain)
                    *open_drain = p->open_drain;
                return 0;
            }
        }
    return -1;
}
