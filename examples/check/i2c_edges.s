; I2C clock edges and data changes for Check mode. Virtual pin 0 is SCL,
; 1 is SDA; the alternate WAIT condition is "SDA changed since dispatch".
;
; Every event leaves exactly 2 cycles after the WAIT that sees its edge
; ends, so all codes (and those of i2c_cond.s) share one latency and the
; host measures intervals from the record timestamps alone. That needs the
; instructions between the WAIT and the EMIT to be buffered already: each
; WAIT sits in the odd half of a word, so the next word is fetched while it
; waits, and both targets of the JMP TOUT lie in those two words.
; Edges closer than about 12 cycles to the previous event can be missed
; while the lane gets back to its WAIT.
.program i2c_edges
.window 0
.start hi_w
.define SCL_FALL 1
.define SCL_RISE 2
.define SDA_CHG 3
.config LANE_CFG 0x0A400000         ; alt: PIN 5 (SDA changed), pol 1

lo:     EMIT SDA_CHG                ; SDA moved while SCL is low
lo_w:   WAIT 1, PIN, 0, alt         ; SCL high, or SDA moved
        JMP TOUT, lo
        EMIT SCL_RISE
        DLY 0
hi_w:   WAIT 0, PIN, 0              ; SCL low
        DLY 0                       ; the JMP TOUT slot of the other path
        EMIT SCL_FALL
        JMP ALWAYS, lo_w
