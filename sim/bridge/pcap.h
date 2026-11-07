// SPDX-License-Identifier: Apache-2.0
#ifndef TSE_PCAP_H
#define TSE_PCAP_H

// Classic libpcap file writer (LINKTYPE_ETHERNET). Timestamps are
// model time, not wall time: cycle / TSE_HZ seconds since reset,
// so Wireshark shows what the chip saw, back-to-back frames and
// all, even though Linux saw them a second apart.

#include <stdint.h>
#include <stdio.h>

typedef struct Pcap {
    FILE*         f;
    unsigned long records;
} Pcap;

int  pcap_open(Pcap* p, const char* path);
void pcap_put(Pcap* p, const uint8_t* frame, int len, uint64_t cycle);
void pcap_close(Pcap* p);

#endif
