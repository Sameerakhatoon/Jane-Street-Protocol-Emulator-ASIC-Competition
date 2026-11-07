// SPDX-License-Identifier: Apache-2.0
// USB/IP framing: replay what `usbip list -r` and `usbip attach` send, then
// the first URBs of enumeration, fed in awkward splits, against a
// behavioural low-speed device that answers GET_DESCRIPTOR(device).
#include <stdio.h>
#include <string.h>
#include "usbip.h"

static int fails = 0;

static void check(int ok, const char* what){
    printf("%s %s\n", ok ? "ok  " : "FAIL", what);
    fails += !ok;
}

static const uint8_t DEV_DESC[18] = {
    18, 1, 0x10, 0x01, 0, 0, 0, 8,          // USB 1.1, EP0 8 bytes
    0x09, 0x12, 0x01, 0x00, 0x00, 0x01,     // VID 0x1209, PID 0x0001 (test IDs), bcdDevice 1.00
    0, 0, 0, 1};

static int submit(void* ctx, const UsbipUrb* u, uint8_t* in, uint32_t* actual){
    int* calls = (int*)ctx;
    (*calls)++;
    if(u->ep == 0 && u->setup[0] == 0x80 && u->setup[1] == 6 && u->setup[3] == 1){
        uint32_t n = u->setup[6] | u->setup[7] << 8;
        *actual = n < sizeof(DEV_DESC) ? n : sizeof(DEV_DESC);
        memcpy(in, DEV_DESC, *actual);
        return 0;
    }
    if(u->ep == 0 && u->setup[0] == 0x00 && u->setup[1] == 9){   // SET_CONFIGURATION
        *actual = 0;
        return 0;
    }
    *actual = 0;
    return -32;                             // stall
}

static int put32(uint8_t* p, uint32_t v){
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
    return 4;
}

static uint32_t get32(const uint8_t* p){
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}

static int cmd_submit(uint8_t* b, uint32_t seq, uint32_t dir, uint32_t ep, uint32_t len, const uint8_t* setup){
    int n = 0;
    n += put32(b + n, USBIP_CMD_SUBMIT);
    n += put32(b + n, seq);
    n += put32(b + n, (1 << 16) | 2);       // devid: bus 1, device 2
    n += put32(b + n, dir);
    n += put32(b + n, ep);
    n += put32(b + n, 0);                   // transfer_flags
    n += put32(b + n, len);
    n += put32(b + n, 0);
    n += put32(b + n, 0);
    n += put32(b + n, 0);
    memcpy(b + n, setup, 8);
    return n + 8;
}

static void feed_split(Usbip* s, const uint8_t* b, int n, int chunk){
    for(int i = 0; i < n; i += chunk)
        usbip_feed(s, b + i, n - i < chunk ? n - i : chunk);
}

int main(void){
    UsbipDev d;
    memset(&d, 0, sizeof(d));
    strcpy(d.path, "/sys/devices/tse/usb1/1-1");
    strcpy(d.busid, "1-1");
    d.busnum = 1;
    d.devnum = 2;
    d.speed = USB_SPEED_LOW;
    d.id_vendor = 0x1209;
    d.id_product = 0x0001;
    d.bcd_device = 0x0100;
    d.config_value = 1;
    d.num_configs = 1;
    d.num_ifs = 1;
    d.if_class[0] = 3;                      // HID boot keyboard
    d.if_subclass[0] = 1;
    d.if_protocol[0] = 1;
    int calls = 0;
    UsbipBackend be = {&calls, submit};
    Usbip s;
    usbip_init(&s, &d, &be);
    uint8_t r[2048];

    // 1: OP_REQ_DEVLIST, one byte at a time
    const uint8_t devlist[8] = {0x01, 0x11, 0x80, 0x05, 0, 0, 0, 0};
    feed_split(&s, devlist, 8, 1);
    int n = usbip_take(&s, r, sizeof(r));
    check(n == 12 + 312 + 4, "1 devlist reply is 12 + 312 + 4 bytes");
    check(r[2] == 0x00 && r[3] == 0x05 && get32(r + 8) == 1, "1 OP_REP_DEVLIST with one device");
    check(!strcmp((char*)r + 12 + 256, "1-1") && get32(r + 12 + 296) == USB_SPEED_LOW,
          "1 busid 1-1, low speed");
    check(r[12 + 300] == 0x12 && r[12 + 301] == 0x09 && r[12 + 312] == 3, "1 VID 0x1209, interface class HID");

    // 2: OP_REQ_IMPORT of a wrong busid, then the right one
    uint8_t imp[40] = {0x01, 0x11, 0x80, 0x03, 0, 0, 0, 0};
    strcpy((char*)imp + 8, "9-9");
    usbip_feed(&s, imp, 40);
    n = usbip_take(&s, r, sizeof(r));
    check(n == 8 && get32(r + 4) == 1 && !s.imported, "2 unknown busid refused");
    memset(imp + 8, 0, 32);
    strcpy((char*)imp + 8, "1-1");
    feed_split(&s, imp, 40, 7);
    n = usbip_take(&s, r, sizeof(r));
    check(n == 8 + 312 && get32(r + 4) == 0 && s.imported, "2 import accepted, device follows");

    // 3: CMD_SUBMIT GET_DESCRIPTOR(device, 64) and SET_CONFIGURATION(1) back to back, split
    uint8_t urbs[256];
    const uint8_t get_desc[8] = {0x80, 6, 0, 1, 0, 0, 64, 0};
    const uint8_t set_cfg[8] = {0x00, 9, 1, 0, 0, 0, 0, 0};
    int k = cmd_submit(urbs, 1, USBIP_DIR_IN, 0, 64, get_desc);
    k += cmd_submit(urbs + k, 2, USBIP_DIR_OUT, 0, 0, set_cfg);
    feed_split(&s, urbs, k, 13);
    n = usbip_take(&s, r, sizeof(r));
    check(n == 48 + 18 + 48, "3 two RET_SUBMITs, 18 bytes of IN data");
    check(get32(r) == USBIP_RET_SUBMIT && get32(r + 4) == 1 && get32(r + 20) == 0 && get32(r + 24) == 18,
          "3 seqnum 1, status 0, actual 18");
    check(!memcmp(r + 48, DEV_DESC, 18), "3 device descriptor data");
    check(get32(r + 66) == USBIP_RET_SUBMIT && get32(r + 70) == 2 && get32(r + 86) == 0, "3 seqnum 2 ok");

    // 4: OUT data is carried, an unknown request stalls, UNLINK answers
    uint8_t out_urb[64];
    const uint8_t vendor[8] = {0x40, 0x55, 0, 0, 0, 0, 4, 0};
    k = cmd_submit(out_urb, 3, USBIP_DIR_OUT, 0, 4, vendor);
    memcpy(out_urb + k, "\x01\x02\x03\x04", 4);
    usbip_feed(&s, out_urb, k + 4);
    n = usbip_take(&s, r, sizeof(r));
    check(n == 48 && (int32_t)get32(r + 20) == -32, "4 vendor OUT request stalls (-EPIPE)");
    uint8_t unl[48] = {0};
    put32(unl, USBIP_CMD_UNLINK);
    put32(unl + 4, 4);
    put32(unl + 20, 3);
    usbip_feed(&s, unl, 48);
    n = usbip_take(&s, r, sizeof(r));
    check(n == 48 && get32(r) == USBIP_RET_UNLINK && get32(r + 4) == 4, "4 RET_UNLINK for seqnum 4");
    check(calls == 3 && s.submits == 3 && s.unlinks == 1 && s.errors == 1, "4 counters");

    // 5: garbage is a protocol error
    const uint8_t bad[48] = {0, 0, 0, 9};
    check(usbip_feed(&s, bad, 48) == -1, "5 unknown command rejected");
    return fails ? 1 : 0;
}
