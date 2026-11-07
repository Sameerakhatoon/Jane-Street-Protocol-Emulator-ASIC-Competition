// SPDX-License-Identifier: Apache-2.0
#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
#include "serprog.h"

#define IN_BUF      8192

enum { ACK = 0x06, NAK = 0x15 };
enum {
    S_CMD_NOP         = 0x00,
    S_CMD_Q_IFACE     = 0x01,
    S_CMD_Q_CMDMAP    = 0x02,
    S_CMD_Q_PGMNAME   = 0x03,
    S_CMD_Q_SERBUF    = 0x04,
    S_CMD_Q_BUSTYPE   = 0x05,
    S_CMD_Q_WRNMAXLEN = 0x08,
    S_CMD_SYNCNOP     = 0x10,
    S_CMD_Q_RDNMAXLEN = 0x11,
    S_CMD_S_BUSTYPE   = 0x12,
    S_CMD_O_SPIOP     = 0x13,
    S_CMD_S_SPI_FREQ  = 0x14,
};
#define BUS_SPI     (1 << 3)

typedef struct Serprog {
    int        lfd;
    int        cfd;
    int        port;
    uint8_t    in[IN_BUF];
    int        nin;
    int        waiting;             // an O_SPIOP is running on the backend
    SpiJob     job;
    SpiBackend be;
    uint64_t   now;
    uint64_t   flash_due;           // behavioural flash state
    SpiJob     flash_job;
} Serprog;

static void flash_start(void* ctx, const SpiJob* job, uint64_t cycle){
    Serprog* s = (Serprog*)ctx;
    s->flash_job = *job;
    s->flash_due = cycle + 2000;
}

static int flash_done(void* ctx, uint64_t cycle, uint8_t* rd){
    Serprog* s = (Serprog*)ctx;
    const SpiJob* j = &s->flash_job;
    if(cycle < s->flash_due)
        return 0;
    memset(rd, 0xff, j->nread);
    if(j->nout >= 1 && j->out[0] == 0x9f){
        static const uint8_t id[3] = {0xef, 0x40, 0x18};
        for(int i = 0; i < j->nread && i < 3; i++)
            rd[i] = id[i];
    }else if(j->nout >= 4 && j->out[0] == 0x03){
        uint32_t addr = (j->out[1] << 16) | (j->out[2] << 8) | j->out[3];
        for(int i = 0; i < j->nread; i++)
            rd[i] = (uint8_t)(addr + i);
    }
    return 1;
}

static void send_bytes(Serprog* s, const uint8_t* b, int n){
    if(s->cfd >= 0 && write(s->cfd, b, n) != n)
        perror("serprog: write");
}

static void reply(Serprog* s, uint8_t status, const uint8_t* data, int n){
    uint8_t r[1 + 64];
    r[0] = status;
    if(n > 64)
        n = 64;
    if(n)
        memcpy(r + 1, data, n);
    send_bytes(s, r, 1 + n);
}

static uint32_t le24(const uint8_t* p){
    return p[0] | (p[1] << 8) | (p[2] << 16);
}

static void parse(Serprog* s){
    while(!s->waiting && s->nin > 0){
        uint8_t  cmd = s->in[0];
        uint8_t* p = s->in + 1;
        int      need = 1;
        if(cmd == S_CMD_S_BUSTYPE)
            need = 2;
        else if(cmd == S_CMD_S_SPI_FREQ)
            need = 5;
        else if(cmd == S_CMD_O_SPIOP)
            need = s->nin >= 7 ? 7 + (int)le24(p) : 7;
        if(s->nin < need)
            return;                         // rest of the command not here yet

        switch(cmd){
        case S_CMD_NOP:
            reply(s, ACK, NULL, 0);
            break;
        case S_CMD_SYNCNOP: {
            uint8_t r[2] = {NAK, ACK};
            send_bytes(s, r, 2);
            break;
        }
        case S_CMD_Q_IFACE: {
            uint8_t v[2] = {1, 0};
            reply(s, ACK, v, 2);
            break;
        }
        case S_CMD_Q_CMDMAP: {
            static const uint8_t cmds[] = {
                S_CMD_NOP, S_CMD_Q_IFACE, S_CMD_Q_CMDMAP, S_CMD_Q_PGMNAME, S_CMD_Q_SERBUF,
                S_CMD_Q_BUSTYPE, S_CMD_Q_WRNMAXLEN, S_CMD_SYNCNOP, S_CMD_Q_RDNMAXLEN,
                S_CMD_S_BUSTYPE, S_CMD_O_SPIOP, S_CMD_S_SPI_FREQ};
            uint8_t map[32] = {0};
            for(size_t i = 0; i < sizeof(cmds); i++)
                map[cmds[i] / 8] |= (uint8_t)(1 << (cmds[i] % 8));
            reply(s, ACK, map, 32);
            break;
        }
        case S_CMD_Q_PGMNAME: {
            uint8_t name[16] = "tse-sim";
            reply(s, ACK, name, 16);
            break;
        }
        case S_CMD_Q_SERBUF: {
            uint8_t v[2] = {0x00, 0x10};    // 4096, little endian
            reply(s, ACK, v, 2);
            break;
        }
        case S_CMD_Q_BUSTYPE: {
            uint8_t v = BUS_SPI;
            reply(s, ACK, &v, 1);
            break;
        }
        case S_CMD_Q_WRNMAXLEN:
        case S_CMD_Q_RDNMAXLEN: {
            uint8_t v[3] = {0x00, 0x01, 0x00};  // 256
            reply(s, ACK, v, 3);
            break;
        }
        case S_CMD_S_BUSTYPE:
            reply(s, p[0] == BUS_SPI ? ACK : NAK, NULL, 0);
            break;
        case S_CMD_S_SPI_FREQ:
            reply(s, ACK, p, 4);            // claim we run at the requested rate
            break;
        case S_CMD_O_SPIOP: {
            int nout = (int)le24(p), nread = (int)le24(p + 3);
            if(nout > SPI_MAX_LEN || nread > SPI_MAX_LEN){
                reply(s, NAK, NULL, 0);
                break;
            }
            memcpy(s->job.out, p + 6, nout);
            s->job.nout = nout;
            s->job.nread = nread;
            s->be.start(s->be.ctx, &s->job, s->now);
            s->waiting = 1;
            break;
        }
        default:
            reply(s, NAK, NULL, 0);
        }
        s->nin -= need;
        memmove(s->in, s->in + need, s->nin);
    }
}

static int sp_init(Adapter* a, Pins* p){
    Serprog* s = (Serprog*)a->ctx;
    (void)p;
    s->lfd = socket(AF_INET, SOCK_STREAM, 0);
    int one = 1;
    setsockopt(s->lfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in sa;
    memset(&sa, 0, sizeof(sa));
    sa.sin_family = AF_INET;
    sa.sin_port = htons((uint16_t)s->port);
    sa.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if(bind(s->lfd, (struct sockaddr*)&sa, sizeof(sa)) < 0 || listen(s->lfd, 1) < 0){
        perror("serprog: bind");
        close(s->lfd);
        return -1;
    }
    socklen_t len = sizeof(sa);
    getsockname(s->lfd, (struct sockaddr*)&sa, &len);
    s->port = ntohs(sa.sin_port);
    a->fd = s->lfd;
    return 0;
}

static int sp_poll(Adapter* a, short revents){
    Serprog* s = (Serprog*)a->ctx;
    if(!(revents & (POLLIN | POLLHUP | POLLERR)))
        return s->waiting;
    if(s->cfd < 0){
        s->cfd = accept(s->lfd, NULL, NULL);
        if(s->cfd >= 0){
            int one = 1;
            setsockopt(s->cfd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
            a->fd = s->cfd;
        }
        return s->waiting;
    }
    ssize_t n = read(s->cfd, s->in + s->nin, IN_BUF - s->nin);
    if(n <= 0){
        close(s->cfd);
        s->cfd = -1;
        s->nin = 0;
        s->waiting = 0;
        a->fd = s->lfd;                     // back to waiting for the next flashrom
        return 0;
    }
    s->nin += (int)n;
    parse(s);
    return s->waiting;
}

static void sp_tick(Adapter* a, Pins* p, uint64_t cycle){
    Serprog* s = (Serprog*)a->ctx;
    uint8_t  rd[SPI_MAX_LEN];
    (void)p;
    s->now = cycle;
    if(s->waiting && s->be.done(s->be.ctx, cycle, rd)){
        uint8_t r[1 + SPI_MAX_LEN];
        r[0] = ACK;
        memcpy(r + 1, rd, s->job.nread);
        send_bytes(s, r, 1 + s->job.nread);
        s->waiting = 0;
        parse(s);                           // flashrom may have queued more
    }
}

static void sp_teardown(Adapter* a){
    Serprog* s = (Serprog*)a->ctx;
    if(s->cfd >= 0)
        close(s->cfd);
    if(s->lfd >= 0)
        close(s->lfd);
    free(s);
    free(a);
}

Adapter* serprog_new(int port){
    Adapter* a = (Adapter*)calloc(1, sizeof(Adapter));
    Serprog* s = (Serprog*)calloc(1, sizeof(Serprog));
    s->lfd = -1;
    s->cfd = -1;
    s->port = port;
    s->be.ctx = s;
    s->be.start = flash_start;
    s->be.done = flash_done;
    a->name = "serprog";
    a->ctx = s;
    a->fd = -1;
    a->init = sp_init;
    a->poll = sp_poll;
    a->tick = sp_tick;
    a->teardown = sp_teardown;
    return a;
}

int serprog_port_of(Adapter* a){
    return ((Serprog*)a->ctx)->port;
}

void serprog_set_backend(Adapter* a, const SpiBackend* b){
    ((Serprog*)a->ctx)->be = *b;
}
