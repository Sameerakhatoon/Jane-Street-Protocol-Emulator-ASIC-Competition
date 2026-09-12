; UART TX, 8 cycles per bit at the lane clock, busy flag on side-set pin 2
; (expected words in uart_tx.hex)
.program uart_tx
.window 0
.start 0
.config LANE_CFG 0x60                     ; ss_base 2, side_en 1
        SET PINS, 1, mask=0b0001          ; idle high: level first ...
        SET PINDIRS, 0b1101, mask=0b1111  ; ... then direction, so no low glitch
frame:  PULL block
        SET X, 7
        SET PINS, 0, mask=0b0001 side 1   ; start bit
        DLY 6 side 1
.align 2
bit:    OUT PINS, 1 [6] side 1            ; 7 cycles
        JMP XDEC, bit side 1              ; 1 cycle, same word: no fetch
        SET PINS, 1, mask=0b0001          ; stop bit
        JMP ALWAYS, frame                 ; same word as the SET: stop bit is 8 cycles
