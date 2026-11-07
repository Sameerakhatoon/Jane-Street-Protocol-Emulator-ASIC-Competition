// SPDX-License-Identifier: Apache-2.0
#include <string.h>
#include "usbip.h"

#define DEV_SIZE    312             // struct usbip_usb_device on the wire

static uint32_t be32(const uint8_t* p){
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}

static uint16_t be16(const uint8_t* p){
    return (uint16_t)(p[0] << 8 | p[1]);
}

static int room(Usbip* s, int n){
    return s->nout + n <= (int)sizeof(s->out);
}

static void put8(Usbip* s, uint8_t v){
    s->out[s->nout++] = v;
}

static void put16(Usbip* s, uint16_t v){
    put8(s, (uint8_t)(v >> 8));
    put8(s, (uint8_t)v);
}

static void put32(Usbip* s, uint32_t v){
    put16(s, (uint16_t)(v >> 16));
    put16(s, (uint16_t)v);
}

static void putn(Usbip* s, const void* p, int n){
    memcpy(s->out + s->nout, p, n);
    s->nout += n;
}

static void put_dev(Usbip* s, const UsbipDev* d){
    putn(s, d->path, 256);
    putn(s, d->busid, 32);
    put32(s, d->busnum);
    put32(s, d->devnum);
    put32(s, d->speed);
    put16(s, d->id_vendor);
    put16(s, d->id_product);
    put16(s, d->bcd_device);
    put8(s, d->dev_class);
    put8(s, d->dev_subclass);
    put8(s, d->dev_protocol);
    put8(s, d->config_value);
    put8(s, d->num_configs);
    put8(s, d->num_ifs);
}

void usbip_init(Usbip* s, const UsbipDev* dev, const UsbipBackend* be){
    memset(s, 0, sizeof(*s));
    s->dev = dev;
    s->be = *be;
}

// Bytes needed for the message at the head of in[], or 0 if not known yet.
static int need(const Usbip* s){
    if(!s->imported){
        if(s->nin < 8)
            return 0;
        return be16(s->in + 2) == OP_REQ_IMPORT ? 40 : 8;
    }
    if(s->nin < 48)
        return 0;
    if(be32(s->in) == USBIP_CMD_SUBMIT && be32(s->in + 12) == USBIP_DIR_OUT)
        return 48 + (int)be32(s->in + 24);
    return 48;
}

static int op(Usbip* s){
    const uint8_t* p = s->in;
    uint16_t code = be16(p + 2);
    if(be16(p) != USBIP_VERSION)
        return -1;
    if(!room(s, 12 + DEV_SIZE + 4 * USBIP_MAX_IFS))
        return -1;
    if(code == OP_REQ_DEVLIST){
        put16(s, USBIP_VERSION);
        put16(s, OP_REP_DEVLIST);
        put32(s, 0);
        put32(s, 1);
        put_dev(s, s->dev);
        for(int i = 0; i < s->dev->num_ifs && i < USBIP_MAX_IFS; i++){
            put8(s, s->dev->if_class[i]);
            put8(s, s->dev->if_subclass[i]);
            put8(s, s->dev->if_protocol[i]);
            put8(s, 0);
        }
        return 0;
    }
    if(code == OP_REQ_IMPORT){
        int ok = !strncmp((const char*)p + 8, s->dev->busid, 32);
        put16(s, USBIP_VERSION);
        put16(s, OP_REP_IMPORT);
        put32(s, ok ? 0 : 1);
        if(ok){
            put_dev(s, s->dev);
            s->imported = 1;
        }
        return 0;
    }
    return -1;
}

static void ret_header(Usbip* s, uint32_t cmd, uint32_t seqnum){
    put32(s, cmd);
    put32(s, seqnum);
    put32(s, 0);                            // devid, direction and ep are 0 in replies
    put32(s, 0);
    put32(s, 0);
}

static int urb(Usbip* s){
    const uint8_t* p = s->in;
    uint32_t cmd = be32(p);
    if(!room(s, 48 + USBIP_MAX_DATA))
        return -1;
    if(cmd == USBIP_CMD_UNLINK){
        // the backend answers every URB at once, so there is never one to unlink
        s->unlinks++;
        ret_header(s, USBIP_RET_UNLINK, be32(p + 4));
        put32(s, 0);
        for(int i = 0; i < 24; i++)
            put8(s, 0);
        return 0;
    }
    if(cmd != USBIP_CMD_SUBMIT)
        return -1;
    UsbipUrb u;
    u.seqnum = be32(p + 4);
    u.devid = be32(p + 8);
    u.dir = be32(p + 12);
    u.ep = be32(p + 16);
    u.flags = be32(p + 20);
    u.length = be32(p + 24);
    u.start_frame = be32(p + 28);
    u.num_packets = be32(p + 32);
    u.interval = be32(p + 36);
    memcpy(u.setup, p + 40, 8);
    if(u.length > USBIP_MAX_DATA)
        return -1;
    if(u.dir == USBIP_DIR_OUT)
        memcpy(u.data, p + 48, u.length);
    uint8_t  in[USBIP_MAX_DATA];
    uint32_t actual = 0;
    int st = s->be.submit(s->be.ctx, &u, in, &actual);
    if(st)
        s->errors++;
    if(u.dir == USBIP_DIR_IN && actual > u.length)
        actual = u.length;
    s->submits++;
    ret_header(s, USBIP_RET_SUBMIT, u.seqnum);
    put32(s, (uint32_t)st);
    put32(s, actual);
    put32(s, 0);                            // start_frame
    put32(s, 0xffffffff);                   // number_of_packets: not isochronous
    put32(s, 0);                            // error_count
    for(int i = 0; i < 8; i++)
        put8(s, 0);
    if(u.dir == USBIP_DIR_IN)
        putn(s, in, (int)actual);
    return 0;
}

int usbip_feed(Usbip* s, const uint8_t* b, int n){
    while(n > 0){
        int k = (int)sizeof(s->in) - s->nin;
        if(k > n)
            k = n;
        memcpy(s->in + s->nin, b, k);
        s->nin += k;
        b += k;
        n -= k;
        for(;;){
            int m = need(s);
            if(m > (int)sizeof(s->in))
                return -1;
            if(!m || s->nin < m)
                break;
            if((s->imported ? urb(s) : op(s)) < 0)
                return -1;
            s->nin -= m;
            memmove(s->in, s->in + m, s->nin);
        }
    }
    return 0;
}

int usbip_take(Usbip* s, uint8_t* dst, int max){
    int n = s->nout < max ? s->nout : max;
    memcpy(dst, s->out, n);
    s->nout -= n;
    memmove(s->out, s->out + n, s->nout);
    return n;
}
