// CAN bit-level helpers: CRC-15, stuffing, frame build and parse.
#include <stdio.h>
#include <string.h>
#include "can_bits.h"

static int fails = 0;

static void check(int ok, const char* what){
    printf("%s %s\n", ok ? "ok  " : "FAIL", what);
    fails += !ok;
}

static int longest_run(const uint8_t* b, int n){
    int best = 0, run = 0;
    for(int i = 0; i < n; i++){
        run = i && b[i] == b[i - 1] ? run + 1 : 1;
        if(run > best)
            best = run;
    }
    return best;
}

int main(void){
    uint8_t bits[CAN_MAX_BITS], st[CAN_MAX_BITS], back[CAN_MAX_BITS];
    int     n = 0, err;

    // 1: the catalogue check value of CRC-15/CAN
    const char* chk = "123456789";
    for(int i = 0; chk[i]; i++)
        for(int k = 7; k >= 0; k--)
            bits[n++] = (uint8_t)((chk[i] >> k) & 1);
    check(can_crc15(bits, n) == 0x059E, "1 crc15(\"123456789\") = 0x059E");

    // 2: frame 0x123 [4] DE AD BE EF
    CanMsg m, got;
    memset(&m, 0, sizeof(m));
    m.id = 0x123;
    m.dlc = 4;
    memcpy(m.data, "\xde\xad\xbe\xef", 4);
    n = can_frame_bits(&m, bits);
    check(n == CAN_HDR_BITS + 32 + 15, "2 frame is 19 + 32 + 15 bits before stuffing");
    check(can_parse(bits, n, &got) == 0 && got.id == 0x123 && got.dlc == 4
          && !memcmp(got.data, m.data, 4), "2 parse gives the frame back");
    int ns = can_stuff(bits, n, st);
    check(ns > n && longest_run(st, ns) <= 5, "2 stuffed, no run longer than 5");
    int nb = can_destuff(st, ns, back, &err);
    check(!err && nb == n && !memcmp(back, bits, n), "2 destuff round trip");
    bits[25] ^= 1;
    check(can_parse(bits, n, &got) == -1, "2 one flipped bit fails the CRC");

    // 3: worst case for stuffing, id 0 and zero data
    memset(&m, 0, sizeof(m));
    m.dlc = 8;
    n = can_frame_bits(&m, bits);
    ns = can_stuff(bits, n, st);
    check(ns - n >= 10 && longest_run(st, ns) <= 5, "3 all-zero frame gets many stuff bits");
    nb = can_destuff(st, ns, back, &err);
    check(!err && nb == n, "3 round trip");
    st[5] = st[4];                          // six equal bits
    can_destuff(st, ns, back, &err);
    check(err == 1, "3 six equal bits are a stuff error");

    // 4: wire form has the 10-bit recessive tail; remote frames carry no data
    int nw = can_wire_bits(&m, st);
    check(nw == ns + 10 && st[nw - 1] == 1 && st[ns] == 1, "4 tail of 10 recessive bits");
    memset(&m, 0, sizeof(m));
    m.id = 0x7ff;
    m.rtr = 1;
    m.dlc = 2;
    n = can_frame_bits(&m, bits);
    check(n == CAN_HDR_BITS + 15 && can_parse(bits, n, &got) == 0 && got.rtr && got.dlc == 2,
          "4 remote frame 0x7FF, dlc 2, no data bits");

    // 5: lower id wins arbitration: first differing id bit is 0 for 0x0FF against 0x100
    CanMsg a, b;
    memset(&a, 0, sizeof(a));
    memset(&b, 0, sizeof(b));
    a.id = 0x100;
    b.id = 0x0FF;
    uint8_t wa[CAN_MAX_BITS], wb[CAN_MAX_BITS];
    can_wire_bits(&a, wa);
    can_wire_bits(&b, wb);
    int k = 0;
    while(wa[k] == wb[k])
        k++;
    check(k == 3 && wa[k] == 1 && wb[k] == 0, "5 0x0FF goes dominant first, at ID bit 8");
    return fails ? 1 : 0;
}
