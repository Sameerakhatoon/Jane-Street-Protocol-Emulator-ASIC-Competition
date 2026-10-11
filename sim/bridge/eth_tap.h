#ifndef TSE_ETH_TAP_H
#define TSE_ETH_TAP_H

// 10BASE-T bridge: Linux TAP device on the host side, Manchester
// bits on two chip pins. The adapter plays the remote PHY.
//
//   Linux -> chip  a frame read from the TAP gets padding, FCS,
//                  preamble and SFD, and is Manchester coded at
//                  3 cycles per half-bit (10 Mbit/s at 60 MHz) onto
//                  cfg.rd. One logic level stands for the pair
//                  (1 = TD+ above TD-); the line idles low.
//   chip -> Linux  cfg.td is decoded (an edge is accepted 3/4 bit
//                  after the last one), the FCS checked, bad frames
//                  dropped and counted, never repaired.
//
// Behind the chip sits a host model: eth_host_arp_icmp answers ARP and ICMP echo for
// 10.0.0.2 with MAC 02:00:00:00:00:02. A decoded frame whose source
// is that MAC came from the host side and goes to the TAP; any other
// frame addressed to it (or broadcast) goes to the host model, whose
// reply is coded back into cfg.rd. So both directions cross the chip.
//
// Needs CAP_NET_ADMIN for TUNSETIFF; run inside
//   unshare --user --map-root-user --net
// which needs no root (see sim/tests/06-tap-ping.sh).

#include "adapter.h"

#define ETH_HALF        3               // cycles per half-bit at 60 MHz
#define ETH_MAX_FRAME   1518

typedef struct EthCfg {
    const char* ifname;
    const char* pcap_path;  // NULL: no capture
    PinRef      rd;         // chip receive input we drive
    PinRef      td;         // chip transmit output we decode
} EthCfg;

Adapter* eth_tap_new(const EthCfg* cfg);

// Host model: build a reply to `in` into `out`. Returns its length,
// or 0 for no reply.
int eth_host_arp_icmp(const uint8_t* in, int n, uint8_t* out, const uint8_t mac[6]);

#endif
