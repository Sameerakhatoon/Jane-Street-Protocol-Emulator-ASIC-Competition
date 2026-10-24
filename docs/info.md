## How it works

This tile holds a UART test design: a transmitter, a receiver and an echo mode. It runs from the 60 MHz project clock and uses 115200 baud, 8 data bits, no parity, one stop bit (8N1), least significant bit first, line idle high.

- Send mode (`uio[1]` = 0): put a byte on `ui_in[7:0]` and raise `uio[0]`. Each rising edge of `uio[0]` sends the byte once on `uo_out[0]`.
- Echo mode (`uio[1]` = 1): `ui_in[3]` becomes the UART receive line, and every byte received there is sent back on `uo_out[0]`. Rising edges on `uio[0]` are ignored.

`uo_out[1]` is high while a frame is being sent. `uo_out[2]` toggles once for every byte received. `uo_out[3]` goes high when a received frame has a low stop bit and stays high until the next good byte; a frame with a bad stop bit is not echoed.

Every input pin passes through a two-flop synchronizer. One numerically controlled oscillator makes a tick at 16 times the baud rate for both directions. The receiver checks the start bit at its middle, so glitches shorter than half a bit are ignored. A one-byte holding register sits between receiver and transmitter, so a byte that arrives while the previous echo is finishing its stop bit is kept, not dropped.

The baud rate is fixed in hardware relative to the clock: 115200 baud at 60 MHz, 96000 baud at 50 MHz.

### TSE (in development)

TSE (Timed Spec Engine) is a protocol engine in development for this tile; the UART above is its test design. A protocol is written once as a timed specification. A compiler turns it into programs for two cycle-exact lanes and proves that every response lands inside its timing window, or rejects the program. The lanes share one SRAM through fixed time slots, so no lane can disturb another's timing. Hardware assists (CRC, bit stuffing, line codecs, clock recovery and response rules) do the bit-level work. The full chip has three modes: Speak (act as one side of a protocol), Check (watch a bus and report timing violations) and Trigger (fire an output on a sequence of events). None of this is in the UART test design.

## How to test

On the demo board, select the project, set the project clock to 60 MHz and release reset (`rst_n` high).

Send a byte:

1. Keep `uio[1]` low.
2. Set `ui_in[7:0]` to the byte, for example 0x41.
3. Take `uio[0]` low, then high. `uo_out[1]` goes high, and `uo_out[0]` carries one 8N1 frame at 115200 baud.

Watch `uo_out[0]` with a 3.3 V USB serial adapter (115200 8N1) or a logic analyzer on the output Pmod header. From the MicroPython SDK on the board (select the mode before the project, and give the released `rst_n` a moment to rise):

```python
import time
from ttboard.demoboard import DemoBoard
from ttboard.mode import RPMode
tt = DemoBoard.get()
tt.mode = RPMode.ASIC_RP_CONTROL   # before enable(), which applies config.ini
tt.shuttle.tt_um_tse_sk.enable()
tt.clock_project_PWM(60_000_000)   # moves the RP2350 to 120 MHz
tt.uio_oe_pico.value = 0b11      # RP2350 drives uio[1:0]
tt.uio_in.value = 0b00
tt.reset_project(True)
tt.reset_project(False)          # releases rst_n to the board pull-up
time.sleep_ms(10)
tt.ui_in.value = 0x41
tt.uio_in.value = 0b01           # rising edge on uio[0]: sends 'A'
tt.uio_in.value = 0b00
```

Echo:

1. Set `uio[1]` high.
2. Send 115200 8N1 serial data into `ui_in[3]`. On the demo board this pin is the RP2350's UART1 TX (GPIO20), so `machine.UART(1, 115200, tx=machine.Pin(20))` can send it; a USB serial adapter on the input Pmod header also works if nothing else drives `ui_in[3]`.
3. Each byte comes back on `uo_out[0]`, and `uo_out[2]` toggles once per byte. Leave at least one idle bit time between bytes when sending back to back at full rate.

## External hardware

None required. A 3.3 V USB serial adapter or a logic analyzer is useful to see `uo_out[0]`.
