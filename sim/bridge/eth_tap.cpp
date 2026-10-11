#include <errno.h>
#include <fcntl.h>
#include <linux/if.h>
#include <linux/if_tun.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include "eth_tap.h"
#include "pcap.h"

#define WAVE_RING   (1u << 21)          // line levels, one per cycle; power of two
#define MAX_BITS    ((ETH_MAX_FRAME + 16) * 8)
#define LINGER      2000                // cycles of time after the last line activity

typedef struct EthTap {
    EthCfg   cfg;
    Pcap     pcap;
    uint8_t  host_mac[6];
    uint8_t* wave;
    unsigned w_head, w_tail;
    uint64_t now;
    int      linger;
    int      rx_active;                 // a frame is arriving on cfg.td
    int      since;                     // cycles since the last accepted edge
    int      prev;
    uint8_t  bits[MAX_BITS];
    int      nbits;
    unsigned long n_tap_in, n_tap_out, n_bad;
} EthTap;

static uint32_t crc32_eth(const uint8_t* p, int n){
    uint32_t c = 0xffffffffu;
    for(int i = 0; i < n; i++){
        c ^= p[i];
        for(int k = 0; k < 8; k++)
            c = (c >> 1) ^ (0xedb88320u & (0u - (c & 1)));
    }
    return ~c;
}

static uint16_t csum16(const uint8_t* p, int n){
    uint32_t s = 0;
    for(int i = 0; i + 1 < n; i += 2)
        s += (p[i] << 8) | p[i + 1];
    if(n & 1)
        s += p[n - 1] << 8;
    while(s >> 16)
        s = (s & 0xffff) + (s >> 16);
    return (uint16_t)~s;
}

int eth_host_arp_icmp(const uint8_t* in, int n, uint8_t* f, const uint8_t mac[6]){
    static const uint8_t ip[4] = {10, 0, 0, 2};
    if(n < 42 || n > ETH_MAX_FRAME)
        return 0;
    memcpy(f, in, n);
    uint16_t type = (f[12] << 8) | f[13];
    if(type == 0x0806 && f[21] == 1 && !memcmp(&f[38], ip, 4)){
        memcpy(&f[0], &in[6], 6);
        memcpy(&f[6], mac, 6);
        f[21] = 2;
        memcpy(&f[32], &in[22], 10);    // target = old sender, MAC + IP
        memcpy(&f[22], mac, 6);
        memcpy(&f[28], ip, 4);
        return 42;
    }
    if(type == 0x0800 && f[23] == 1 && !memcmp(&f[30], ip, 4)){
        int ihl = (f[14] & 0x0f) * 4, tot = (f[16] << 8) | f[17];
        if(14 + tot > n || tot < ihl + 8)
            return 0;
        uint8_t* icmp = &f[14 + ihl];
        if(icmp[0] != 8)
            return 0;
        memcpy(&f[0], &in[6], 6);
        memcpy(&f[6], mac, 6);
        memcpy(&f[26], &in[30], 4);
        memcpy(&f[30], &in[26], 4);
        icmp[0] = 0;
        icmp[2] = icmp[3] = 0;
        uint16_t c = csum16(icmp, tot - ihl);
        icmp[2] = c >> 8;
        icmp[3] = c & 0xff;
        return 14 + tot;
    }
    return 0;
}

static unsigned wave_used(const EthTap* e){
    return e->w_head - e->w_tail;
}

static void wave_put(EthTap* e, uint8_t level, int count){
    for(int i = 0; i < count; i++)
        e->wave[e->w_head++ & (WAVE_RING - 1)] = level;
}

// What the remote PHY puts on the wire: preamble, SFD, frame, FCS,
// TP_IDL, interframe gap.
static void encode(EthTap* e, const uint8_t* in, int n){
    uint8_t fr[8 + ETH_MAX_FRAME];
    if(n > ETH_MAX_FRAME - 4)
        return;
    unsigned need = (unsigned)(8 + 64 + n) * 8 * 2 * ETH_HALF + 220 * 2 * ETH_HALF;
    if(WAVE_RING - wave_used(e) < need){
        fprintf(stderr, "eth: line busy, frame dropped\n");
        return;
    }
    if(e->pcap.f)
        pcap_put(&e->pcap, in, n, e->now);
    memset(fr, 0x55, 7);
    fr[7] = 0xd5;
    memcpy(fr + 8, in, n);
    if(n < 60){
        memset(fr + 8 + n, 0, 60 - n);
        n = 60;
    }
    uint32_t fcs = crc32_eth(fr + 8, n);
    for(int i = 0; i < 4; i++)
        fr[8 + n + i] = (uint8_t)(fcs >> (8 * i));
    n += 12;
    for(int i = 0; i < n; i++)
        for(int k = 0; k < 8; k++){
            int bit = (fr[i] >> k) & 1;     // LSB first; 1 = low then high
            wave_put(e, !bit, ETH_HALF);
            wave_put(e, bit, ETH_HALF);
        }
    wave_put(e, 1, 6 * ETH_HALF);           // TP_IDL, about 3 bit times
    wave_put(e, 0, 96 * 2 * ETH_HALF);      // interframe gap
}

static void finish(Adapter* a, EthTap* e){
    static const uint8_t bcast[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
    uint8_t fr[ETH_MAX_FRAME + 8];
    int i = 1, n = 0;
    while(i < e->nbits && !(e->bits[i - 1] == 1 && e->bits[i] == 1))
        i++;                                // SFD ends ...1,0,1,1
    for(int j = i + 1; j + 8 <= e->nbits && n < (int)sizeof(fr); j += 8){
        uint8_t b = 0;
        for(int k = 0; k < 8; k++)
            b |= (uint8_t)(e->bits[j + k] << k);
        fr[n++] = b;
    }
    if(n < 64){
        e->n_bad++;
        return;
    }
    uint32_t got;
    memcpy(&got, &fr[n - 4], 4);
    if(crc32_eth(fr, n - 4) != got){
        e->n_bad++;
        fprintf(stderr, "eth: bad FCS, frame dropped\n");
        return;
    }
    n -= 4;
    if(!memcmp(fr + 6, e->host_mac, 6)){            // from the host side: to Linux
        if(e->pcap.f)
            pcap_put(&e->pcap, fr, n, e->now);
        if(write(a->fd, fr, n) < 0)
            perror("eth: tap write");
        else
            e->n_tap_out++;
    }else if(!memcmp(fr, e->host_mac, 6) || !memcmp(fr, bcast, 6)){
        uint8_t r[ETH_MAX_FRAME];
        int rn = eth_host_arp_icmp(fr, n, r, e->host_mac);
        if(rn > 0)
            encode(e, r, rn);
    }
}

static void decode(Adapter* a, EthTap* e, int v){
    e->since++;
    if(v != e->prev){
        if(!e->rx_active && v == 1){
            e->rx_active = 1;
            e->since = 0;
            e->nbits = 0;
            e->bits[e->nbits++] = 1;
        }else if(e->rx_active && e->since >= 5){
            e->since = 0;
            if(e->nbits < MAX_BITS)
                e->bits[e->nbits++] = (uint8_t)v;
        }
    }
    e->prev = v;
    if(e->rx_active && e->since > 3 * ETH_HALF){
        e->rx_active = 0;
        finish(a, e);
    }
}

static int eth_init(Adapter* a, Pins* p){
    EthTap* e = (EthTap*)a->ctx;
    int fd = open("/dev/net/tun", O_RDWR | O_NONBLOCK);
    if(fd < 0){
        fprintf(stderr, "eth: open /dev/net/tun: %s (errno=%d)\n", strerror(errno), errno);
        return -1;
    }
    struct ifreq ifr;
    memset(&ifr, 0, sizeof(ifr));
    ifr.ifr_flags = IFF_TAP | IFF_NO_PI;
    strncpy(ifr.ifr_name, e->cfg.ifname, IFNAMSIZ - 1);
    if(ioctl(fd, TUNSETIFF, &ifr) < 0){
        fprintf(stderr, "eth: TUNSETIFF %s: %s (errno=%d)\n", e->cfg.ifname,
                strerror(errno), errno);
        close(fd);
        return -1;
    }
    if(e->cfg.pcap_path && pcap_open(&e->pcap, e->cfg.pcap_path) < 0){
        close(fd);
        return -1;
    }
    a->fd = fd;
    pin_set(p, e->cfg.rd, 0);
    return 0;
}

static int eth_poll(Adapter* a, short revents){
    EthTap* e = (EthTap*)a->ctx;
    if(revents & POLLIN){
        uint8_t b[2048];
        ssize_t n;
        while((n = read(a->fd, b, sizeof(b))) > 0){
            e->n_tap_in++;
            encode(e, b, (int)n);
        }
    }
    return wave_used(e) || e->rx_active || e->linger > 0;
}

static void eth_tick(Adapter* a, Pins* p, uint64_t cycle){
    EthTap* e = (EthTap*)a->ctx;
    e->now = cycle;
    if(e->linger > 0)
        e->linger--;
    if(wave_used(e)){
        pin_set(p, e->cfg.rd, e->wave[e->w_tail++ & (WAVE_RING - 1)]);
        e->linger = LINGER;
    }else{
        pin_set(p, e->cfg.rd, 0);
    }
    decode(a, e, pin_get(p, e->cfg.td));
}

static void eth_teardown(Adapter* a){
    EthTap* e = (EthTap*)a->ctx;
    fprintf(stderr, "eth: %lu frames from tap, %lu to tap, %lu bad, %lu pcap records\n",
            e->n_tap_in, e->n_tap_out, e->n_bad, e->pcap.records);
    pcap_close(&e->pcap);
    if(a->fd >= 0)
        close(a->fd);
    free(e->wave);
    free(e);
    free(a);
}

Adapter* eth_tap_new(const EthCfg* cfg){
    static const uint8_t mac[6] = {0x02, 0, 0, 0, 0, 0x02};
    Adapter* a = (Adapter*)calloc(1, sizeof(Adapter));
    EthTap*  e = (EthTap*)calloc(1, sizeof(EthTap));
    e->cfg = *cfg;
    e->wave = (uint8_t*)malloc(WAVE_RING);
    memcpy(e->host_mac, mac, 6);
    a->name = "eth";
    a->ctx = e;
    a->fd = -1;
    a->init = eth_init;
    a->poll = eth_poll;
    a->tick = eth_tick;
    a->teardown = eth_teardown;
    return a;
}
