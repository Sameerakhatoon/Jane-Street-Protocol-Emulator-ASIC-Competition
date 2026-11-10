# TSE architecture

This page describes the chip block by block, as built in `src/`. The instruction encodings and every register field are in [isa.md](isa.md), generated from the same tables as the RTL. The numbers come from the 8x4 full-chip layout (run t8x4b) unless marked otherwise.

## The chip at a glance

```
             ui_in[7:0]   uio[7:0]                         uo_out[7:0]
                 |           |                                   ^
        +--------v-----------v-----------------------------------+--------+
        |  pin crossbar: synchronizers, sample delay, open drain, idle    |
        +---+----------------+-------------------+---------------+--------+
            | 4 virtual pins | 4 virtual pins    |               |
        +---v------+     +---v------+      +-----v-----+   +-----+------+
        |  lane 0  |     |  lane 1  |      | event unit|   | host SPI / |
        | seq +    |     | seq +    |----->| timestamp |   | loader     |
        | assists  |     | assists  |      | log, trig |   | (commands) |
        +---^------+     +---^------+      +-----------+   +-----+------+
            | fetch slots    |                                   |
        +---+----------------+-----------------------------------v------+
        |  slot arbiter + program SRAM (512 x 32, two IHP 512x16 macros) |
        |  + data window + 8-word fallback store + boot self-test        |
        +----------------------------------------------------------------+
```

| Block | File | Role |
|---|---|---|
| Tiny Tapeout top | `tt_um_tse_sk_chip.sv`, `tse_chip.sv` | the pins, load mode and run mode |
| Lane | `tse_lane.sv`, `tse_seq.sv` | sequencer, assists and response rules |
| Assists | `tse_crc.sv`, `tse_stuffer.sv`, `tse_codec.sv`, `tse_dpll.sv`, `tse_timer.sv`, `tse_nco.sv` | bit-level work without instructions |
| Response rules | `tse_resp_rule.sv` | answer a bit pattern inside the bit |
| Program store | `tse_prog_mem.sv`, `tse_slot_arb.sv`, `tse_sram_512x32.sv` | shared SRAM with fixed slots |
| Configuration | `tse_cfg_bank.sv`, `tse_cfg_word.sv` | latch banks, written in 3 cycles |
| Pin crossbar | `tse_xbar.sv`, `tse_sync.sv`, `tse_pin_delay.sv` | any lane pin on any pad |
| Event unit | `tse_event_unit.sv` | timestamps, event log, three-stage trigger |
| Host interface | `tse_loader.sv`, `tse_host_spi.sv` | the load byte bus and the runtime SPI |
| Self-test | `tse_selftest.sv` | March C- on the SRAM and a pad loopback |

## Two modes of the pins

The chip has two states, chosen by `rst_n` (`tse_chip.sv`):

- **Load mode** (`rst_n` low). `ui_in` is a byte bus from the host: `uio_in[0]` is the strobe, `uio_in[1]` marks a command byte, and `uo_out` returns the readback byte. The host writes programs and configuration, runs the self-test and commits the load with a CRC-32. Every power-up starts here, because the flops do not start at 0 and the configuration is lost when the design is deselected.
- **Run mode** (`rst_n` high). All 16 pads belong to the crossbar, except `uo_out[3]` (MISO) while the runtime SPI is enabled. The host reaches the registers and the mailboxes over SPI on `ui_in[0..2]`.

The pin multiplexer switches only when `rst_n` changes, so a lane never sees a load-mode pin.

## The lane

A lane is a small sequencer with exact timing plus hardware assists. There are two lanes (`LANES = 2`).

**Sequencer** (`tse_seq.sv`). 16-bit instructions, one dispatched per cycle unless a delay, a `WAIT` or a slot wait holds it. Registers X and Y, the timeout counter T, the 32-bit shift registers ISR and OSR, and the clock C that counts cycles from RUN. Encodings come from the generated `tse_isa_pkg.sv`, so the RTL holds no instruction numbers of its own. A reserved encoding halts the lane at dispatch, with no effect, and raises an interrupt.

**Fetch.** A lane reads a 128-word window of the SRAM (two instructions per word). It may fetch only in its own slot (one cycle in four) and keeps the last two words in a fetch buffer, so a tight loop runs without waiting for its slot. The compiler models the buffer exactly.

**Assists**, each configured by lane registers and free of protocol names:

| Assist | What it does | Used by (examples in `specs/`) |
|---|---|---|
| CRC (`tse_crc.sv`) | any width 5 to 32, any polynomial, init, final XOR, reflection | CRC-5 and CRC-16 for USB, CRC-15 for CAN, CRC-32 for Ethernet |
| Stuffer (`tse_stuffer.sv`) | insert or remove an opposite bit after n equal bits | n = 5 for CAN, n = 6 for USB |
| Codec (`tse_codec.sv`) | NRZ, NRZI or Manchester, optionally inverted | Manchester for 10BASE-T, NRZI for USB |
| DPLL (`tse_dpll.sv`) | recover a bit clock from edges, phase in 1/16 cycle, resync limit, dual-edge sampling | CAN bit timing, 10BASE-T and USB receive |
| Timer (`tse_timer.sv`, `tse_nco.sv`) | an NCO tick (rate = f_clk x inc / 2^24) and a 16-bit down counter | baud rates at 60 or 50 MHz, the 10BASE-T link pulse |

**Response rules** (`tse_resp_rule.sv`). Two per lane. A rule compares the last bits received with a pattern and mask at a chosen bit position, optionally gated by the CRC verdict, then drives up to 16 response bits or aborts the frame. Rule 1 can chain onto rule 0 for a 32-bit pattern. A rule runs in bit mode (on the DPLL's recovered bits) or in pin-edge mode (on edges of a chosen pin, such as SCK). It answers with no program in the loop. That is what lets the compiler treat a target's reaction time as a constant, for example the CAN ACK inside the CRC delimiter bit.

## Program store and the slot frame

One logical 512 x 32 SRAM, built from two IHP 512x16 macros that share address and control (`tse_sram_512x32.sv`; two narrower macros leave more room for routing above them). It holds four windows of 128 words: windows 0 and 1 hold the lanes' programs and window 3 is the data window.

The slot arbiter (`tse_slot_arb.sv`) counts a 16-cycle frame:

| Cycle in the frame | Owner |
|---|---|
| cycle mod 4 == 0 | lane 0 fetch |
| cycle mod 4 == 1 | lane 1 fetch |
| cycle mod 4 == 2 | idle (a third lane's slot) |
| 3, 7 | lane 0 and lane 1 data-window access (`LDW`, `STW`) |
| 11 | idle |
| 15 | host access |

Each grant depends only on the count and the requester's own request, so one lane can never change another lane's timing. This non-interference is proved with SymbiYosys (`formal/arb/`). RUN changes only at the frame boundary, so a lane's slots have a fixed phase from its start, which the compiler uses.

An 8-word **fallback store** in latches can hold a small program. A lane fetches from it in the same slot with the same latency, so a program behaves identically from either, and the fallback works even if the SRAM fails its self-test.

## Configuration banks

Assist and rule settings are class-L registers held in latch banks (`tse_cfg_bank.sv`), 32 words of 32 bits per lane plus the crossbar and event banks. A latch bit measures 27.6 um^2 against 71.2 um^2 for a flop. Writes use a three-edge protocol driven from flops, so there is no clock gate and no glitch on the latch enable; a lane's `CSRW` to a configuration word therefore takes 3 cycles. The banks are cleared by the loader at BEGIN, not by `rst_n`. Programs cannot read configuration.

## Pin crossbar

The crossbar (`tse_xbar.sv`) connects 16 lane virtual pins (4 per lane) to 16 pads: `uo_out[7:0]` and `uio[7:0]`.

- **Inputs.** 24 sources (`ui_in`, `uio_in` and the chip's own outputs, so a lane can watch a pad another lane drives), each synchronized exactly once: two rising-edge flops, plus a falling-edge capture half a cycle earlier for dual-edge timing. Each lane input has an inversion bit and a programmable sample delay of 0 to 7 half cycles (`tse_pin_delay.sv`), used to line up a sample point with the pad round trip.
- **Outputs.** Each pad picks a source pin, with inversion, open drain and an idle level that holds while its lane is stopped. The output registers have no reset, so the idle level survives every mode switch.
- **Loopback.** The self-test feeds the pad outputs back to the inputs to check both samplers.

## Event unit

The event unit (`tse_event_unit.sv`) gives Check and Trigger modes one time base for both lanes:

- A 32-bit chip timestamp. A lane's `EMIT` sends a 6-bit code and 8 data bits; the record is stamped with the cycle of the `EMIT` and queued in a FIFO the host reads (`EV_POP`). A TIME record before each new 65,536-cycle epoch lets the host rebuild full timestamps. Dropped events are counted.
- A three-stage sequence trigger. Each stage matches a lane mask, a code and data under masks, with an optional window in cycles since the previous stage. The last stage fires a pulse of programmable width on a pad, writes a TRIG record and can stop lanes.
- Four chip flags written by `SIG` and visible to every lane the next cycle.

## Host interface, loading and self-test

One command decoder (`tse_loader.sv`) serves both front ends: the byte bus in load mode and the SPI target in run mode (`tse_host_spi.sv`, mode 0, SCK up to about f_clk / 8). Commands: WR, RD, burst WRB and RDB, BEGIN, BIST, COMMIT and STAT. A command byte always restarts the parser, so a random power-up state or a truncated command does nothing.

Loading a program, as the host library does it (`host/tse_host/`):

1. BEGIN: clear `loaded`, stop the lanes, reset the running CRC-32 and clear the configuration.
2. BIST: March C- on the SRAM through the macro's test port, twice with two backgrounds, then a walking-one and walking-zero pad loopback; about 11,500 cycles. The result is kept through the switch to run mode. If the SRAM fails, the host loads the program into the fallback store instead.
3. WRB: write the program words and configuration.
4. COMMIT with the CRC-32 of every address and data byte written: `loaded` is set only when it matches; STAT reports it.
5. Read every burst back and compare, then release `rst_n` and start the lanes with RUN.

In run mode BEGIN, BIST and COMMIT are refused while a lane runs, and a refusal sets a sticky error bit.

## Clocks, area and timing

| Item | Value |
|---|---|
| Clock | 60 MHz from the RP2350 (50 MHz fallback; the NCO timers keep baud rates exact at either) |
| Tiles | 8x4 |
| Standard cells | 29,164; utilisation about 40 percent, 44 percent with the SRAM macros |
| Setup slack at 60 MHz | +1.75 ns slow corner, +4.39 ns typical, +4.32 ns fast |
| Hold slack | +0.159 ns fast corner, +0.30 ns typical, +0.46 ns slow |

The full signoff and the gate-level results are in [../VERIFICATION.md](../VERIFICATION.md).

## Why it is built this way

- **Lanes with a sequencer, not a CPU.** A lane has no cache, no interrupts in the program flow and no variable-latency memory, so the compiler can count every cycle and prove a timing window rather than estimate it.
- **Generic assists, not protocol blocks.** The same CRC, stuffer, codec and DPLL serve CAN, USB and Ethernet with different settings, and a new protocol is a new spec, not new silicon.
- **Fixed slots.** Two lanes running at once cannot disturb each other's timing, which is proved, not measured.
- **Latch configuration.** Assist and rule settings take most of the configuration area; latches cost less than half the area of flops.
