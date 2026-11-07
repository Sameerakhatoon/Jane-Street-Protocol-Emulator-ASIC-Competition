// SPDX-License-Identifier: Apache-2.0
#ifndef TSE_CAN_NODE_H
#define TSE_CAN_NODE_H

// SocketCAN bridge: a bit-level remote CAN node on the wired-AND bus that
// the chip's CAN lanes share.
//
// Host side, one of:
//   IF             a raw AF_CAN socket on a SocketCAN interface (vcan0);
//                  needs the vcan module, which stock WSL kernels lack
//   unix:PATH      a listening Unix stream socket; the client reads and
//                  writes struct can_frame records (16 bytes), exactly what
//                  a raw CAN socket carries
// Frames from the host are sent on the bus; frames received from the bus
// with a good CRC are written back to the host. Base data and remote
// frames only; error frames are not generated.
//
// Bus side, 60 clocks per bit by default (1 Mbit/s at 60 MHz), sample
// point at 80 percent of the bit (clock 48), hard sync on the falling edge
// of a start of frame. The node is the checker for the chip's output: it
// destuffs every frame on the bus, recomputes CRC-15, counts stuff and CRC
// errors, drives the ACK slot for a good frame it did not send, and for its
// own frames checks that some other node (the chip) acknowledged and handles
// lost arbitration by sending again once the bus is idle. A frame nobody
// acknowledges is sent again up to 16 times, then dropped.

#include "adapter.h"
#include "wired_and.h"

#define CAN_BIT_CLOCKS      60

typedef struct CanStats {
    unsigned long tx, rx, arb_lost, no_ack, stuff_err, crc_err, acked_by_us;
} CanStats;

Adapter* can_node_new(const char* name, const char* host, WiredAnd* bus, int bit_clocks);
const char* can_node_where(Adapter* a);             // interface name or socket path
void     can_node_stats(Adapter* a, CanStats* s);

#endif
