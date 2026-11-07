// SPDX-License-Identifier: Apache-2.0
#include <errno.h>
#include <linux/can.h>
#include <linux/can/raw.h>
#include <net/if.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include "can_bits.h"
#include "can_node.h"

#define TXQ         32
#define MAX_RETRY   16

enum { ST_FIELD, ST_STUFF_TAIL, ST_TAIL };

typedef struct CanNode {
    const char* name;
    char        where[108];
    int         unix_mode;
    int         lfd, cfd;           // listening socket (unix mode) and the data socket
    uint8_t     rbuf[sizeof(struct can_frame)];
    int         nrbuf;
    WiredAnd*   bus;
    int         drv;

    CanMsg      q[TXQ];
    int         qh, qn, retries;

    uint8_t     txb[CAN_MAX_BITS];
    int         ntx, txpos, sending;

    int         bit, sample;        // clocks per bit, sample clock
    int         phase, prev, idle, need, in_frame, stage, tail, ack_now, acked;
    uint8_t     d[CAN_MAX_BITS];
    int         nd, expect, run, last, crc_ok;
    uint64_t    cycle;
    CanStats    st;
} CanNode;

static void host_write(CanNode* c, const CanMsg* m){
    struct can_frame f;
    memset(&f, 0, sizeof(f));
    f.can_id = m->id | (m->rtr ? CAN_RTR_FLAG : 0);
    f.len = m->dlc > 8 ? 8 : m->dlc;
    memcpy(f.data, m->data, can_data_len(m->dlc, m->rtr));
    if(c->cfd >= 0 && write(c->cfd, &f, sizeof(f)) != (ssize_t)sizeof(f))
        perror("can: write");
}

static void queue_push(CanNode* c, const CanMsg* m, int front){
    if(c->qn == TXQ){
        fprintf(stderr, "can %s: transmit queue full, frame dropped\n", c->name);
        return;
    }
    if(front){
        c->qh = (c->qh + TXQ - 1) % TXQ;
        c->q[c->qh] = *m;
    }else{
        c->q[(c->qh + c->qn) % TXQ] = *m;
    }
    c->qn++;
}

static void frame_reset(CanNode* c, int need){
    c->in_frame = 0;
    c->sending = 0;
    c->ack_now = 0;
    c->idle = 0;
    c->need = need;
}

static void abort_frame(CanNode* c, const char* why){
    fprintf(stderr, "can %s: %s at cycle %llu\n", c->name, why, (unsigned long long)c->cycle);
    frame_reset(c, 11);
}

static void frame_done(CanNode* c){
    CanMsg m;
    int ok = c->crc_ok && can_parse(c->d, c->nd, &m) == 0;
    if(c->sending){
        if(c->acked){
            c->st.tx++;
            fprintf(stderr, "can %s: tx %03X acked at cycle %llu\n", c->name, c->q[c->qh].id,
                    (unsigned long long)c->cycle);
            c->qh = (c->qh + 1) % TXQ;
            c->qn--;
            c->retries = 0;
        }else{
            c->st.no_ack++;
            if(++c->retries >= MAX_RETRY){
                fprintf(stderr, "can %s: tx %03X never acknowledged, dropped\n", c->name, c->q[c->qh].id);
                c->qh = (c->qh + 1) % TXQ;
                c->qn--;
                c->retries = 0;
            }
        }
    }else if(ok){
        c->st.rx++;
        fprintf(stderr, "can %s: rx %03X [%d] at cycle %llu\n", c->name, m.id, m.dlc,
                (unsigned long long)c->cycle);
        host_write(c, &m);
    }
    frame_reset(c, 3);                      // intermission
}

// One sampled bit while a frame is on the bus.
static void rx_bit(CanNode* c, int b){
    int mine = c->sending ? c->txb[c->txpos] : 1;
    if(c->stage == ST_TAIL){
        if(c->tail == 0)
            c->ack_now = c->crc_ok && !c->sending;      // drive the next bit, the ACK slot
        if(c->tail == 1){
            c->acked = !b;
            if(c->ack_now)
                c->st.acked_by_us++;
            c->ack_now = 0;
        }
        if(++c->tail == 10)
            frame_done(c);
        return;
    }
    if(c->sending && mine != b){
        if(mine == 1 && c->nd <= 12){           // recessive sent, dominant seen: lost arbitration
            c->st.arb_lost++;
            c->sending = 0;
            wired_and_drive(c->bus, c->drv, 1);
            fprintf(stderr, "can %s: lost arbitration at cycle %llu\n", c->name,
                    (unsigned long long)c->cycle);
        }else{
            abort_frame(c, "bit error");
            return;
        }
    }
    if(c->run == 5){                            // this must be a stuff bit
        if(b == c->last){
            c->st.stuff_err++;
            abort_frame(c, "stuff error");
            return;
        }
        c->last = b;
        c->run = 1;
        if(c->stage == ST_STUFF_TAIL){
            c->stage = ST_TAIL;
            c->tail = 0;
        }
        return;
    }
    c->d[c->nd++] = (uint8_t)b;
    c->run = b == c->last ? c->run + 1 : 1;
    c->last = b;
    if(c->nd == CAN_HDR_BITS){
        int dlc = (c->d[15] << 3) | (c->d[16] << 2) | (c->d[17] << 1) | c->d[18];
        c->expect = CAN_HDR_BITS + 8 * can_data_len(dlc, c->d[12]) + 15;
    }
    if(c->nd == c->expect){
        uint16_t got = 0;
        for(int i = c->nd - 15; i < c->nd; i++)
            got = (uint16_t)((got << 1) | c->d[i]);
        c->crc_ok = can_crc15(c->d, c->nd - 15) == got;
        if(!c->crc_ok){
            c->st.crc_err++;
            fprintf(stderr, "can %s: crc error at cycle %llu\n", c->name, (unsigned long long)c->cycle);
        }
        c->stage = c->run == 5 ? ST_STUFF_TAIL : ST_TAIL;
        c->tail = 0;
    }
}

static int cn_init(Adapter* a, Pins* p){
    CanNode* c = (CanNode*)a->ctx;
    (void)p;
    c->drv = wired_and_attach(c->bus);
    if(c->drv < 0)
        return -1;
    if(c->unix_mode){
        struct sockaddr_un sa;
        memset(&sa, 0, sizeof(sa));
        sa.sun_family = AF_UNIX;
        memcpy(sa.sun_path, c->where, strlen(c->where));   // where[] is shorter than sun_path
        unlink(c->where);
        c->lfd = socket(AF_UNIX, SOCK_STREAM, 0);
        if(c->lfd < 0 || bind(c->lfd, (struct sockaddr*)&sa, sizeof(sa)) < 0 || listen(c->lfd, 1) < 0){
            perror("can: unix socket");
            return -1;
        }
        a->fd = c->lfd;
        return 0;
    }
    c->cfd = socket(PF_CAN, SOCK_RAW, CAN_RAW);
    if(c->cfd < 0){
        perror("can: AF_CAN socket (no CAN support in this kernel?)");
        return -1;
    }
    struct ifreq ifr;
    memset(&ifr, 0, sizeof(ifr));
    size_t len = strlen(c->where);
    if(len >= IFNAMSIZ){
        fprintf(stderr, "can: interface name too long: %s\n", c->where);
        return -1;
    }
    memcpy(ifr.ifr_name, c->where, len);
    if(ioctl(c->cfd, SIOCGIFINDEX, &ifr) < 0){
        fprintf(stderr, "can: no interface %s (vcan module missing?)\n", c->where);
        return -1;
    }
    struct sockaddr_can sa;
    memset(&sa, 0, sizeof(sa));
    sa.can_family = AF_CAN;
    sa.can_ifindex = ifr.ifr_ifindex;
    if(bind(c->cfd, (struct sockaddr*)&sa, sizeof(sa)) < 0){
        perror("can: bind");
        return -1;
    }
    a->fd = c->cfd;
    return 0;
}

static int cn_busy(CanNode* c){
    return c->qn > 0 || c->in_frame;
}

static int cn_poll(Adapter* a, short revents){
    CanNode* c = (CanNode*)a->ctx;
    if(!(revents & (POLLIN | POLLHUP | POLLERR)))
        return cn_busy(c);
    if(c->unix_mode && c->cfd < 0){
        c->cfd = accept(c->lfd, NULL, NULL);
        if(c->cfd >= 0)
            a->fd = c->cfd;
        return cn_busy(c);
    }
    ssize_t n = read(c->cfd, c->rbuf + c->nrbuf, sizeof(c->rbuf) - c->nrbuf);
    if(n <= 0){
        if(n < 0 && errno == EINTR)
            return cn_busy(c);
        if(c->unix_mode){
            close(c->cfd);
            c->cfd = -1;
            c->nrbuf = 0;
            a->fd = c->lfd;
        }
        return cn_busy(c);
    }
    c->nrbuf += (int)n;
    if(c->nrbuf == (int)sizeof(struct can_frame)){
        struct can_frame f;
        memcpy(&f, c->rbuf, sizeof(f));
        c->nrbuf = 0;
        if(f.can_id & (CAN_EFF_FLAG | CAN_ERR_FLAG)){
            fprintf(stderr, "can %s: only base frames, dropped %08X\n", c->name, f.can_id);
        }else{
            CanMsg m;
            memset(&m, 0, sizeof(m));
            m.id = (uint16_t)(f.can_id & CAN_SFF_MASK);
            m.rtr = (f.can_id & CAN_RTR_FLAG) ? 1 : 0;
            m.dlc = f.len > 8 ? 8 : f.len;
            memcpy(m.data, f.data, 8);
            queue_push(c, &m, 0);
        }
    }
    return cn_busy(c);
}

static void cn_tick(Adapter* a, Pins* p, uint64_t cycle){
    CanNode* c = (CanNode*)a->ctx;
    int level = wired_and_level(c->bus);
    (void)p;
    c->cycle = cycle;
    if(!c->in_frame && c->prev && !level){      // hard sync on the start of frame
        c->in_frame = 1;
        c->phase = 1;
        c->stage = ST_FIELD;
        c->nd = 0;
        c->run = 0;
        c->last = 2;
        c->expect = -1;
        c->crc_ok = 0;
        c->acked = 0;
    }
    c->prev = level;
    if(c->phase == 0){
        if(!c->in_frame && !c->sending && c->qn && c->idle >= c->need){
            c->ntx = can_wire_bits(&c->q[c->qh], c->txb);
            c->txpos = 0;
            c->sending = 1;
        }
        int v = c->sending ? c->txb[c->txpos] : 1;
        if(c->ack_now)
            v = 0;
        wired_and_drive(c->bus, c->drv, v);
    }
    if(c->phase == c->sample){
        if(c->in_frame)
            rx_bit(c, level);
        else if(level && c->idle < 1000)
            c->idle++;
    }
    if(++c->phase == c->bit){
        c->phase = 0;
        if(c->sending && ++c->txpos >= c->ntx)
            c->sending = 0;
    }
}

static void cn_teardown(Adapter* a){
    CanNode* c = (CanNode*)a->ctx;
    fprintf(stderr, "can %s: tx %lu rx %lu arbitration lost %lu no ack %lu stuff errors %lu "
            "crc errors %lu acked by us %lu\n", c->name, c->st.tx, c->st.rx, c->st.arb_lost,
            c->st.no_ack, c->st.stuff_err, c->st.crc_err, c->st.acked_by_us);
    if(c->cfd >= 0)
        close(c->cfd);
    if(c->lfd >= 0){
        close(c->lfd);
        unlink(c->where);
    }
    free(c);
    free(a);
}

Adapter* can_node_new(const char* name, const char* host, WiredAnd* bus, int bit_clocks){
    Adapter* a = (Adapter*)calloc(1, sizeof(Adapter));
    CanNode* c = (CanNode*)calloc(1, sizeof(CanNode));
    c->name = name;
    c->unix_mode = !strncmp(host, "unix:", 5);
    strncpy(c->where, c->unix_mode ? host + 5 : host, sizeof(c->where) - 1);
    c->lfd = -1;
    c->cfd = -1;
    c->bus = bus;
    c->prev = 1;
    c->need = 11;
    c->bit = bit_clocks;
    c->sample = bit_clocks * 4 / 5;
    a->name = "can";
    a->ctx = c;
    a->fd = -1;
    a->init = cn_init;
    a->poll = cn_poll;
    a->tick = cn_tick;
    a->teardown = cn_teardown;
    return a;
}

const char* can_node_where(Adapter* a){
    return ((CanNode*)a->ctx)->where;
}

void can_node_stats(Adapter* a, CanStats* s){
    *s = ((CanNode*)a->ctx)->st;
}
