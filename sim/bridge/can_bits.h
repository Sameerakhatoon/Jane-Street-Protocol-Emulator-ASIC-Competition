// SPDX-License-Identifier: Apache-2.0
#ifndef TSE_CAN_BITS_H
#define TSE_CAN_BITS_H

// Classical CAN base frames (11-bit ID) at the bit level, written apart from
// the chip's CRC and stuffing units so it can judge their output.
//
//   frame bits  SOF, ID[10:0], RTR, IDE, r0, DLC[3:0], data, CRC[14:0]
//   stuffing    after 5 equal bits the sender inserts one opposite bit;
//               covers SOF through CRC; 6 equal bits there are an error
//   tail        CRC delimiter 1, ACK slot (1 sent, 0 from a receiver),
//               ACK delimiter 1, EOF 7 x 1, all unstuffed
//   CRC-15      x^15+x^14+x^10+x^8+x^7+x^4+x^3+1 (0x4599), init 0, MSB first;
//               0x059E over the ASCII bits of "123456789"
//
// Bits are one per byte, 0 = dominant, 1 = recessive.

#include <stdint.h>

#define CAN_MAX_BITS    160         // stuffed base frame with 8 bytes plus tail
#define CAN_HDR_BITS    19          // SOF through DLC

typedef struct CanMsg {
    uint16_t id;
    uint8_t  rtr;
    uint8_t  dlc;                   // 0..15 on the wire, at most 8 data bytes
    uint8_t  data[8];
} CanMsg;

uint16_t can_crc15(const uint8_t* bits, int n);
int      can_data_len(int dlc, int rtr);
int      can_frame_bits(const CanMsg* m, uint8_t* bits);    // SOF..CRC, unstuffed
int      can_stuff(const uint8_t* in, int n, uint8_t* out);
int      can_destuff(const uint8_t* in, int n, uint8_t* out, int* err);
int      can_wire_bits(const CanMsg* m, uint8_t* out);      // stuffed, plus the tail
int      can_parse(const uint8_t* bits, int n, CanMsg* m);  // SOF..CRC unstuffed: 0, or -1 on bad CRC

#endif
