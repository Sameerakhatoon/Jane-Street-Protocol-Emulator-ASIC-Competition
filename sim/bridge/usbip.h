// SPDX-License-Identifier: Apache-2.0
#ifndef TSE_USBIP_H
#define TSE_USBIP_H

// USB/IP server framing (skeleton, protocol only). Linux's vhci-hcd
// attaches to a server like this one with `usbip attach -r HOST -b BUSID`
// over TCP port 3240, and from then on the Linux USB stack sees the device.
//
// Two phases on one connection, every field big-endian:
//   operations   OP_REQ_DEVLIST (0x8005) -> OP_REP_DEVLIST (0x0005)
//                OP_REQ_IMPORT  (0x8003) -> OP_REP_IMPORT  (0x0003)
//                each starts with version 0x0111, code, status
//   URBs         after a good import: USBIP_CMD_SUBMIT (1) -> USBIP_RET_SUBMIT (3)
//                USBIP_CMD_UNLINK (2) -> USBIP_RET_UNLINK (4), 48-byte headers
//
// The parser takes bytes as they arrive (any split) and appends replies to
// an output buffer. URBs go to a UsbipBackend, which answers at once.

#include <stdint.h>

#define USBIP_PORT          3240
#define USBIP_VERSION       0x0111
#define USBIP_MAX_DATA      1024
#define USBIP_MAX_IFS       4

enum {
    OP_REQ_DEVLIST = 0x8005, OP_REP_DEVLIST = 0x0005,
    OP_REQ_IMPORT  = 0x8003, OP_REP_IMPORT  = 0x0003,
    USBIP_CMD_SUBMIT = 1, USBIP_CMD_UNLINK = 2, USBIP_RET_SUBMIT = 3, USBIP_RET_UNLINK = 4,
};
enum { USBIP_DIR_OUT = 0, USBIP_DIR_IN = 1 };
enum { USB_SPEED_LOW = 1, USB_SPEED_FULL = 2 };

typedef struct UsbipDev {
    char     path[256];
    char     busid[32];
    uint32_t busnum, devnum, speed;
    uint16_t id_vendor, id_product, bcd_device;
    uint8_t  dev_class, dev_subclass, dev_protocol;
    uint8_t  config_value, num_configs, num_ifs;
    uint8_t  if_class[USBIP_MAX_IFS], if_subclass[USBIP_MAX_IFS], if_protocol[USBIP_MAX_IFS];
} UsbipDev;

typedef struct UsbipUrb {
    uint32_t seqnum, devid, dir, ep;
    uint32_t flags, length, start_frame, num_packets, interval;
    uint8_t  setup[8];
    uint8_t  data[USBIP_MAX_DATA];      // OUT data as received
} UsbipUrb;

typedef struct UsbipBackend {
    void* ctx;
    // fill in[] for IN transfers, set *actual; return 0 or a negative errno (-32 = stall)
    int (*submit)(void* ctx, const UsbipUrb* u, uint8_t* in, uint32_t* actual);
} UsbipBackend;

typedef struct Usbip {
    const UsbipDev* dev;
    UsbipBackend    be;
    int             imported;
    uint8_t         in[48 + USBIP_MAX_DATA];
    int             nin;
    uint8_t         out[8192];
    int             nout;
    unsigned long   submits, unlinks, errors;
} Usbip;

void usbip_init(Usbip* s, const UsbipDev* dev, const UsbipBackend* be);
int  usbip_feed(Usbip* s, const uint8_t* b, int n);     // 0, or -1 on a protocol error
int  usbip_take(Usbip* s, uint8_t* dst, int max);       // pop up to max reply bytes

#endif
