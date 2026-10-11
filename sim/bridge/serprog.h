#ifndef TSE_SERPROG_H
#define TSE_SERPROG_H

// flashrom serprog bridge, protocol framing only (skeleton).
//
//   flashrom -p serprog:ip=127.0.0.1:PORT
//
// The adapter answers the init sequence itself (NOP, SYNCNOP,
// interface version, command map, programmer name, bus type, buffer
// and length limits, SPI frequency). Only flashrom's first sync uses
// short read timeouts; every later command blocks, so a slow chip is
// fine. Each O_SPIOP (write n bytes, then read m bytes, one CS
// assertion) becomes an SpiJob handed to a SpiBackend; while the job
// runs the adapter is busy, so model time advances until the backend
// says done, then the ACK and read bytes go back.
//
// The backend is a behavioural flash (W25Q128, JEDEC ef 40 18;
// READ 0x03 returns (address + i) & 0xff) that finishes 2000 cycles
// after the job starts.

#include <stddef.h>
#include "adapter.h"

#define SPI_MAX_LEN     256

typedef struct SpiJob {
    uint8_t out[SPI_MAX_LEN];
    int     nout;
    int     nread;
} SpiJob;

typedef struct SpiBackend {
    void* ctx;
    void (*start)(void* ctx, const SpiJob* job, uint64_t cycle);
    int  (*done)(void* ctx, uint64_t cycle, uint8_t* rd);  // 1 when finished, rd filled
} SpiBackend;

Adapter* serprog_new(int port);                     // 0 = pick a free port
int      serprog_port_of(Adapter* a);
void     serprog_set_backend(Adapter* a, const SpiBackend* b);

#endif
