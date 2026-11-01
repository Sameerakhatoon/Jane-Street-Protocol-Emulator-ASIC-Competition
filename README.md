![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg) ![](../../workflows/fpga/badge.svg)

# TSE: Timed Spec Engine

Write a serial protocol once, as a timed spec. The chip speaks it, checks it and triggers on it, and the compiler proves that every response lands inside its datasheet window, or refuses to build the program.

TSE is a Tiny Tapeout design for the IHP CMOS5L shuttle, 6x4 tiles, 60 MHz. It is in development. Every result below comes from simulation of the RTL in this repository; no chip exists yet.

## What it is

A timed spec is a text file that describes one side of a protocol: its states, what it drives on which wire, and the timing rules from the datasheet ("hold SCL high at least 600 ns", "MISO valid before the next SCK rise"). The compiler, `tsec` (in `tools/tsec/`), turns the spec into programs and configuration words for the chip. Before it writes anything it works out, cycle by cycle, when every output edge and every sample happens, and checks each against its window. If one cannot be met it rejects the spec with the rule, how late the edge is and a suggested fix. A rejected spec produces no image.

The chip runs the result in one of three ways:

- **Speak**: act as a controller or a target of the protocol on real pins.
- **Check**: watch a bus between other devices, timestamp what happens and report every broken timing rule; sweep one threshold a clock at a time to measure a device's margin.
- **Trigger**: decode the traffic and pulse an output pin when a chosen sequence of events happens.

What of this works is in the evidence table below, each row with the command that shows it.

Inside the chip:

- **Two lanes.** A lane is a small processor with 16-bit instructions whose timing is exact to the cycle, plus hardware assists for the bit-level work: a CRC of 5 to 32 bits, a bit stuffer, NRZI and Manchester codecs, a clock-recovery DPLL, timers and two response rules that answer inside a bit. Two protocols, or both sides of one, run at once.
- **One shared SRAM** (512 words of 32 bits) holds the programs and a data window. Each lane fetches in its own fixed time slots, so one lane can never change another lane's timing.
- **A pin crossbar** puts any lane pin on any pad, with open drain and idle levels, and lets a checking lane read pads another lane drives.
- **An event unit** timestamps events from both lanes on one clock and drives the trigger pin from a sequence of up to three stages.
- **A loader** fills the SRAM and the configuration after every power-up, with a self-test of the SRAM and a CRC-32 check, and a fallback store for the case where the SRAM self-test fails. At run time the host reaches the chip over SPI.

The tile holds a UART test design (the datasheet page is `docs/info.md`); the full chip is the top `src/tt_um_tse_sk_chip.sv` with its source list in `info_chip.yaml`, and every test below builds it.

## What we do differently from PIO and PRU

1. **Timing is checked before a program exists.** With PIO or PRU the programmer counts cycles; here the compiler places every edge and rejects a spec it cannot meet. Example: `specs/spi_target.tse` at 4 MHz is rejected (`R314`, the lane reaches its wait for the next SCK rise up to 7 cycles late, fix `f_sck <= 1.99 MHz`); with `-D t_css=117e-9 -D f_sck=1.875e6` it builds.
2. **Protocol assists in every lane.** CRC, bit stuffing, line codes, clock recovery and response rules are hardware, so a lane does not spend instructions per bit on them (PIO has none of these; a PRU does them in software).
3. **Lanes cannot disturb each other.** Memory access is granted by fixed slots, proven with SymbiYosys for the slot arbiter (`formal/arb/`), and measured end to end in `examples/combo/`.

PIO is much smaller and mature, and a PRU is a full CPU; TSE trades both for timing that is stated and checked.

## Evidence

"Works" means the command passes on the current branch.

| Claim | How it is checked | Run it | Status |
|---|---|---|---|
| A UART spec goes through the whole flow: compiled, loaded over the loader pins into the full two-lane chip, run from SRAM; the transmitter sends 46 frames and the receiver takes 30 frames at nominal, +1.5 % and -1.5 % baud rate, all with 0 violations | `examples/uart/`, independent judge `verif/judges/uart.py` on the pins-only trace | `make e2e-uart` | works |
| Every transmit edge lands on the cycle the compiler predicted (worst error 3.48 of 10.18 ticks over 230 edges) | `examples/uart/timing.py` compares the trace with the compiler report | `make e2e-uart` | works |
| Two lanes run at once and the second does not disturb the first: UART on lane 0 keeps every edge on its predicted cycle while lane 1 runs I2C | `examples/combo/` | `make e2e-combo` | works on the UART side |
| I2C Fast-mode controller against a simulated target: both transactions decode, every byte acknowledged and read back | `examples/i2c/` | `make e2e-i2c` | runs; the judge flags SCL high time 583 ns against 600 ns, a known compiler bug (a dropped wait), kept as an expected failure in `examples/tests/03-i2c.sh` |
| The compiler rejects a spec it cannot prove and says why | `specs/spi_target.tse` at 4 MHz (rejected, `R314`), the same spec with `-D t_css=117e-9 -D f_sck=1.875e6` (accepted) | `python3 -m tools.tsec build specs/spi_target.tse` | works |
| 25 specs for 9 protocols (UART, SPI, I2C, CAN, SWD, JTAG, PS/2, USB low speed, 10BASE-T); 49 pinned compiler outcomes: which build, which are rejected and for what reason | `specs/`, `specs/tests/expected-outcomes.txt` | `make specs-test` | works; some specs are rejected, each for the reason recorded in that file (the frame-level specs for USB, 10BASE-T and CAN are not lowered yet) |
| The RTL matches a Python reference model, instruction by instruction, on random programs | lockstep with Hypothesis: `verif/lockstep/` (sequencer), `verif/lane/`, `verif/blocks/` (assists), model in `model/` | `make verif-test` | works |
| CRC matches the catalogue values (CRC-5 and CRC-16 of USB, CRC-15 of CAN, CRC-32); stuffer, codec and DPLL properties; slot arbiter non-interference and slot ownership | SymbiYosys in `formal/`; every proof must also catch planted bugs, and UNKNOWN counts as a failure | `make formal` | works |
| Loading is safe: CRC-32 checked at `COMMIT`, readback, `RUN` refused before a good load, SRAM self-test (March C-) finds stuck, transition, coupling and address faults, fallback store runs when the SRAM is dead | `verif/chip/`, `host/tests/rtl/` | `make chip-test host-rtl` | works |
| Trigger: an event reaches the trigger pin 3 clock edges after the lane emits it | `test/rtl/tb_chip_pins.sv` | `make rtl-sim` | works |
| Check-mode host tools turn event records into timing compliance reports and margin sweeps | `host/tse_check/` | `make tools-test` | works on recorded events; a spec does not compile into a Check program yet |
| Independent judges decode pins-only traces from the published standards (UART, SPI, I2C, JTAG, SWD, CAN, USB low speed, 10BASE-T, PS/2) | `verif/judges/` and its tests | `make judges-test` | works |
| Unmodified OpenOCD reads a JTAG IDCODE and an SWD DPIDR, and Linux `ping` runs over 10BASE-T, through simulated pins | `sim/bridge/`, `verif/judges/demos/` | `make judges-demos judges-run` | works on a small stand-in test top (`sim/rtl/tt_um_uart_echo.sv`), not yet on TSE lanes |
| The RP2350 host code loads and runs the chip through the Tiny Tapeout SDK | `host/tse_rp2/`, tested against a CPython stand-in of the SDK | `make host-test` | works in simulation; not yet run on a board |
| The UART test design that the tile holds | `test/` (Tiny Tapeout cocotb test) | `make rtl-test` | works |
| Full chip hardened with the Tiny Tapeout flow, timing at three corners, precheck | | | not done yet |
| Per-program formal proof of each compiled image against the lane RTL | `tools/tsec/proofgen.py` writes the harness; `make tsec-proof` checks the window checker itself | | not done yet |

## Run it

Everything runs on Linux (Ubuntu 22.04 or 24.04, or WSL2). Needed: the OSS CAD Suite (Verilator, Icarus Verilog, Yosys, SymbiYosys), `g++`, `make`, Python 3.11 with cocotb 2.1, pytest, Hypothesis and PyYAML. The Tiny Tapeout template test in `test/` pins its own versions in `test/requirements.txt`.

All tests, one step after the other, with a log per step in `build/regress/` and a summary table at the end (about 15 minutes):

    make regress

The end-to-end examples (the first run builds the Verilated chip, about 2 minutes):

    make e2e-test            # all examples and their checks
    examples/uart/run.sh     # one example; outputs in build/e2e/uart/
    examples/i2c/run.sh
    examples/combo/run.sh

`examples/uart/run.sh` prints, among its step logs:

    PASS host: 16 bytes sent, 30 received and echoed
    PASS uart: 46 frames, 0 violations
    PASS uart: 30 frames, 0 violations
    PASS timing: every edge at the compiler's cycle, worst error 3.48 of 10.18 ticks
    PASS e2e-uart: 46 TX frames, 30 RX frames judged, 0 violations

Real software against the simulated pins (needs OpenOCD and unprivileged user namespaces):

    make judges-demos judges-run

Compile a spec yourself:

    python3 -m tools.tsec build specs/uart_tx.tse -o build/uart_tx
    python3 -m tools.tsec build specs/spi_target.tse     # rejected, with the reason and a fix

## Repository layout

| Path | What it holds |
|---|---|
| `info.yaml`, `docs/info.md` | Tiny Tapeout project file and datasheet page (the UART test design) |
| `info_chip.yaml` | project file of the full chip |
| `src/` | SystemVerilog RTL: `tt_um_tse_sk_chip.sv` and one file per block; `tse_isa_pkg.sv` and `tse_regs_pkg.sv` are generated |
| `isa/` | instruction set and register map in YAML, the single source of the generated tables |
| `tools/tsec/` | the timed-spec compiler |
| `tools/tseasm/` | assembler, disassembler and table generators |
| `specs/` | timed specs, expected compiler outcomes, judge cases |
| `model/` | Python reference model of the lane, assists and chip |
| `test/` | Tiny Tapeout template test and SystemVerilog testbenches |
| `verif/` | lockstep suites, chip tests, independent protocol judges |
| `formal/` | SymbiYosys proofs and mutation runs |
| `sim/` | Verilator harness with bridges to real software (pty, TAP, OpenOCD, serprog, CAN, USB/IP) |
| `host/` | host library, command line, RP2350 MicroPython code, Check-mode tools |
| `examples/` | end-to-end examples, one folder each |
| `mk/` | make fragments, one per part of the project |

## License

Apache License 2.0, see `LICENSE`.
