#include <string.h>
#include "adapter.h"
#include "pcap.h"

#pragma pack(push, 1)
typedef struct PcapHdr {
    uint32_t magic;             // 0xa1b2c3d4 = us-resolution timestamps
    uint16_t version_major;     // 2
    uint16_t version_minor;     // 4
    int32_t  thiszone;
    uint32_t sigfigs;
    uint32_t snaplen;
    uint32_t network;           // LINKTYPE_ETHERNET = 1
} PcapHdr;

typedef struct PcapRec {
    uint32_t ts_sec;
    uint32_t ts_usec;
    uint32_t incl_len;
    uint32_t orig_len;
} PcapRec;
#pragma pack(pop)

int pcap_open(Pcap* p, const char* path){
    memset(p, 0, sizeof(*p));
    p->f = fopen(path, "wb");
    if(!p->f){
        perror(path);
        return -1;
    }
    PcapHdr h = {0xa1b2c3d4u, 2, 4, 0, 0, 65535, 1};
    fwrite(&h, sizeof(h), 1, p->f);
    fflush(p->f);
    return 0;
}

void pcap_put(Pcap* p, const uint8_t* frame, int len, uint64_t cycle){
    if(!p->f)
        return;
    uint64_t us = cycle / (TSE_HZ / 1000000);
    PcapRec r = {(uint32_t)(us / 1000000), (uint32_t)(us % 1000000),
                 (uint32_t)len, (uint32_t)len};
    fwrite(&r, sizeof(r), 1, p->f);
    fwrite(frame, 1, len, p->f);
    fflush(p->f);
    p->records++;
}

void pcap_close(Pcap* p){
    if(p->f)
        fclose(p->f);
    p->f = NULL;
}
