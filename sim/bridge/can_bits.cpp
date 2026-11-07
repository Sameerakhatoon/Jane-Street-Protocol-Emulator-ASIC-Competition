// SPDX-License-Identifier: Apache-2.0
#include <string.h>
#include "can_bits.h"

uint16_t can_crc15(const uint8_t* bits, int n){
    uint16_t c = 0;
    for(int i = 0; i < n; i++){
        int fb = ((c >> 14) & 1) ^ (bits[i] & 1);
        c = (uint16_t)((c << 1) & 0x7fff);
        if(fb)
            c ^= 0x4599;
    }
    return c;
}

int can_data_len(int dlc, int rtr){
    if(rtr)
        return 0;
    return dlc > 8 ? 8 : dlc;
}

static int put(uint8_t* b, int n, uint32_t v, int w){
    for(int i = w - 1; i >= 0; i--)
        b[n++] = (uint8_t)((v >> i) & 1);
    return n;
}

int can_frame_bits(const CanMsg* m, uint8_t* b){
    int n = 0;
    n = put(b, n, 0, 1);
    n = put(b, n, m->id & 0x7ff, 11);
    n = put(b, n, m->rtr ? 1 : 0, 1);
    n = put(b, n, 0, 2);                    // IDE = 0 (base frame), r0
    n = put(b, n, m->dlc & 15, 4);
    for(int i = 0; i < can_data_len(m->dlc, m->rtr); i++)
        n = put(b, n, m->data[i], 8);
    return put(b, n, can_crc15(b, n), 15);
}

int can_stuff(const uint8_t* in, int n, uint8_t* out){
    int k = 0, run = 0;
    uint8_t last = 2;
    for(int i = 0; i < n; i++){
        out[k++] = in[i];
        run = in[i] == last ? run + 1 : 1;
        last = in[i];
        if(run == 5){
            last = (uint8_t)!last;
            out[k++] = last;
            run = 1;
        }
    }
    return k;
}

int can_destuff(const uint8_t* in, int n, uint8_t* out, int* err){
    int k = 0, run = 0;
    uint8_t last = 2;
    *err = 0;
    for(int i = 0; i < n; i++){
        if(run == 5){
            if(in[i] == last){
                *err = 1;                   // six equal bits
                return k;
            }
            last = in[i];
            run = 1;
            continue;
        }
        out[k++] = in[i];
        run = in[i] == last ? run + 1 : 1;
        last = in[i];
    }
    return k;
}

int can_wire_bits(const CanMsg* m, uint8_t* out){
    uint8_t raw[CAN_MAX_BITS];
    int n = can_stuff(raw, can_frame_bits(m, raw), out);
    for(int i = 0; i < 10; i++)
        out[n++] = 1;                       // CRC delimiter, ACK slot, ACK delimiter, EOF
    return n;
}

static uint32_t get(const uint8_t* b, int at, int w){
    uint32_t v = 0;
    for(int i = 0; i < w; i++)
        v = (v << 1) | b[at + i];
    return v;
}

int can_parse(const uint8_t* b, int n, CanMsg* m){
    if(n < CAN_HDR_BITS + 15 || b[0] != 0)
        return -1;
    memset(m, 0, sizeof(*m));
    m->id = (uint16_t)get(b, 1, 11);
    m->rtr = (uint8_t)b[12];
    m->dlc = (uint8_t)get(b, 15, 4);
    int len = can_data_len(m->dlc, m->rtr);
    if(n != CAN_HDR_BITS + 8 * len + 15)
        return -1;
    for(int i = 0; i < len; i++)
        m->data[i] = (uint8_t)get(b, CAN_HDR_BITS + 8 * i, 8);
    return can_crc15(b, n - 15) == get(b, n - 15, 15) ? 0 : -1;
}
