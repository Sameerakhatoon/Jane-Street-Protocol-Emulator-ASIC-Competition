; I2C START and STOP for Check and Trigger mode. Virtual pin 0 is SCL, 1 is
; SDA; JMP PIN reads SDA; the alternate WAIT condition is "SCL low".
;
; At each SCL high phase the lane reads SDA once, so it knows which change
; would be a START (SDA high now) and which a STOP (SDA low now), then waits
; for that SDA edge or for SCL to fall. The EMIT leaves 2 cycles after the
; WAIT ends, the same latency as every event of i2c_edges.s (see there).
.program i2c_cond
.window 1
.start top
.define START 4
.define STOP 5
.config LANE_CFG 0x00000080         ; jmp_pin 1 (SDA); alt: PIN 0 (SCL), pol 0

top:    WAIT 1, PIN, 0              ; SCL high
        JMP PIN, hi
        DLY 0
lo:     WAIT 1, PIN, 1, alt         ; SDA rises, or SCL falls
        JMP TOUT, top
        EMIT STOP
        JMP ALWAYS, top
hi:     WAIT 0, PIN, 1, alt         ; SDA falls, or SCL falls
        JMP TOUT, top
        EMIT START
        JMP ALWAYS, top
