// SPDX-License-Identifier: Apache-2.0
// WiredAnd resolver and pin names. Prints one line per check, exit 1 on a failure.
#include <stdio.h>
#include <string.h>
#include "pins_gen.h"
#include "wired_and.h"

static int fails = 0;

static void check(int ok, const char* what){
    printf("%s %s\n", ok ? "ok  " : "FAIL", what);
    fails += !ok;
}

static int same(PinRef a, PinRef b){
    return a.bus == b.bus && a.bit == b.bit;
}

int main(void){
    Pins     p;
    WiredAnd w;
    PinRef   xio2, uo4;
    memset(&p, 0, sizeof(p));
    pin_parse("uio2", &xio2);
    pin_parse("uo4", &uo4);

    // 1: host-only bus, two drivers
    WiredAnd h;
    wired_and_init(&h, "h", PinRef{PIN_NONE, 0}, PinRef{PIN_NONE, 0});
    int a = wired_and_attach(&h), b = wired_and_attach(&h);
    check(wired_and_resolve(&h, &p) == 1, "1 idle bus is high (pull-up)");
    wired_and_drive(&h, a, 0);
    check(wired_and_resolve(&h, &p) == 0, "1 one driver low pulls the bus low");
    wired_and_drive(&h, b, 0);
    wired_and_drive(&h, a, 1);
    check(wired_and_resolve(&h, &p) == 0, "1 the other still holds it low");
    wired_and_drive(&h, b, 1);
    check(wired_and_resolve(&h, &p) == 1 && h.falls == 1, "1 released again, one falling edge");

    // 2: chip on a uio pin, resolved level goes back on uio_in
    wired_and_init(&w, "sda", xio2, xio2);
    int m = wired_and_attach(&w);
    p.uio_oe = 0x04;
    p.uio_out = 0x04;
    check(wired_and_resolve(&w, &p) == 1 && (p.uio_in & 4), "2 chip driving 1 counts as released");
    p.uio_out = 0x00;
    check(wired_and_resolve(&w, &p) == 0 && !(p.uio_in & 4), "2 chip oe=1 out=0 pulls low");
    p.uio_oe = 0;
    wired_and_drive(&w, m, 0);
    check(wired_and_resolve(&w, &p) == 0 && !(p.uio_in & 4), "2 model pulls low, chip sees it");
    wired_and_drive(&w, m, 1);
    check(wired_and_resolve(&w, &p) == 1 && (p.uio_in & 4), "2 both released");

    // 3: CAN style, chip TX on a uo pin, RX on a ui pin
    PinRef rx;
    pin_parse("ui6", &rx);
    wired_and_init(&w, "can", uo4, rx);
    p.uo_out = 0x10;
    check(wired_and_resolve(&w, &p) == 1 && (p.ui_in & 0x40), "3 uo pin high is recessive");
    p.uo_out = 0;
    check(wired_and_resolve(&w, &p) == 0 && !(p.ui_in & 0x40), "3 uo pin low is dominant");

    // 4: full house, attach limit
    WiredAnd f;
    wired_and_init(&f, "f", PinRef{PIN_NONE, 0}, PinRef{PIN_NONE, 0});
    int last = 0;
    for(int i = 0; i <= WIRED_MAX_DRIVERS; i++)
        last = wired_and_attach(&f);
    check(last == -1, "4 attach fails past the limit");

    // 6: who drives, for the VCD: a probe with a direction, the chip's oe
    wired_and_init(&w, "swdio", xio2, xio2);
    m = wired_and_attach(&w);
    p.uio_oe = 0;
    p.uio_out = 0;
    wired_and_drive_dir(&w, m, 1, 1);
    check(wired_and_resolve(&w, &p) == 1 && w.host_on && !w.chip_on, "6 host drives a 1: high, host owns it");
    wired_and_drive_dir(&w, m, 1, 0);
    check(wired_and_resolve(&w, &p) == 0 && w.host_on, "6 host drives a 0");
    wired_and_drive_dir(&w, m, 0, 0);
    p.uio_oe = 0x04;
    p.uio_out = 0x04;
    check(wired_and_resolve(&w, &p) == 1 && !w.host_on && w.chip_on, "6 host lets go, chip drives a 1");
    p.uio_oe = 0;
    check(wired_and_resolve(&w, &p) == 1 && !w.host_on && !w.chip_on, "6 nobody drives: pull-up");
    wired_and_drive(&w, m, 0);
    check(wired_and_resolve(&w, &p) == 0 && w.host_on, "6 open-drain pull counts as driving");

    // 5: names from the pinout tables
    PinRef r;
    int    od = 0;
    check(pin_lookup("XIO2", &r, &od) == 0 && same(r, xio2) && od == 1, "5 XIO2 is uio2, open drain");
    check(pin_lookup("run.spi_miso", &r, &od) == 0 && r.bus == PIN_UO && r.bit == 3 && !od,
          "5 RUN.SPI_MISO is uo3");
    check(pin_lookup("LOAD.LD_STROBE", &r, NULL) == 0 && r.bus == PIN_UIO && r.bit == 0, "5 LD_STROBE is uio0");
    check(pin_lookup("UART.UART_TX", &r, NULL) == 0 && r.bus == PIN_UO && r.bit == 0, "5 UART.UART_TX is uo0");
    check(pin_lookup("uio7", &r, NULL) == 0 && r.bus == PIN_UIO && r.bit == 7, "5 raw names still work");
    check(pin_lookup("NOPE", &r, NULL) == -1 && pin_lookup("uo8", &r, NULL) == -1, "5 unknown names fail");
    check(same(PIN_RUN_XIN5, PinRef{PIN_UI, 5}) && same(PIN_LOAD_RB7, PinRef{PIN_UO, 7}),
          "5 generated constants");
    return fails ? 1 : 0;
}
