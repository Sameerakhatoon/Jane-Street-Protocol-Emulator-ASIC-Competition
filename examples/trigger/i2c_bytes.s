; I2C byte decoder for Trigger mode. Virtual pin 0 is SDA (IN PINS, 1
; samples it), 1 is SCL; JMP PIN reads SDA; the alternate WAIT condition is
; "SDA changed since dispatch". Each byte goes out as one event with the
; byte in X[7:0]: ADDR for the first byte after a START or repeated START,
; DATA for the bytes after it. The acknowledge bit is not reported.
.program i2c_bytes
.window 0
.start idle
.define ADDR 8
.define DATA 9
.config LANE_CFG 0x08400000         ; jmp_pin 0 (SDA); alt: PIN 4 (SDA changed), pol 1

idle:   WAIT 1, PIN, 1              ; SCL high
        WAIT 1, PIN, 0              ; SDA high: bus free
        WAIT 0, PIN, 0              ; SDA falls: START
addr:   WAIT 0, PIN, 1              ; first SCL fall after the START
        SET X, 7
abit:   WAIT 1, PIN, 1
        IN PINS, 1                  ; sample SDA while SCL is high
        WAIT 0, PIN, 1, alt         ; SCL falls, or SDA moves: START or STOP
        JMP TOUT, cond
        JMP XDEC, abit
        MOV X, ISR
        EMIT ADDR
        WAIT 1, PIN, 1              ; acknowledge bit
        WAIT 0, PIN, 1
data:   SET X, 7
dbit:   WAIT 1, PIN, 1
        IN PINS, 1
        WAIT 0, PIN, 1, alt
        JMP TOUT, cond
        JMP XDEC, dbit
        MOV X, ISR
        EMIT DATA
        WAIT 1, PIN, 1
        WAIT 0, PIN, 1
        JMP ALWAYS, data
cond:   JMP PIN, idle               ; SDA high: STOP, wait for the next START
        JMP ALWAYS, addr            ; SDA low: repeated START
