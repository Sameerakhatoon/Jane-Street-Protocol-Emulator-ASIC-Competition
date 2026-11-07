![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg) ![](../../workflows/fpga/badge.svg)

# TSE: Timed Spec Engine

Write a serial protocol once, as a timed spec. The chip speaks it, checks it and triggers on it, and the compiler proves that every response lands inside its datasheet window, or refuses to build the program.

TSE is a Tiny Tapeout design for the IHP CMOS5L shuttle, 6x4 tiles. It is in development. Every result below comes from simulation of the RTL in this repository or from the physical-design flow; no chip exists yet, and there is no hardware or FPGA evidence.

## What it is

A timed spec is a text file that describes one side of a protocol: its states, what it drives on which wire, and the timing rules from the datasheet ("hold SCL high at least 600 ns", "MISO valid before the next SCK rise"). The compiler, `tsec` (in `tools/tsec/`), turns the spec into programs and configuration words for the chip. Before it writes anything it works out, cycle by cycle, when every output edge and every sample happens, and checks each against its window. If one cannot be met it rejects the spec with the rule, how late the edge is and a suggested fix. A rejected spec produces no image.

The chip runs the result in one of three modes:

- **Speak**: act as a controller or a target of the protocol on real pins.
- **Check**: watch a bus between other devices, timestamp what happens and report every broken timing rule; sweep one threshold a clock at a time to measure a device's margin.
- **Trigger**: decode the traffic and pulse an output pin when a chosen sequence of events happens.

What of this works, and at what level of evidence, is in the claims table below.

## Architecture

Inside the chip:

- **Two lanes.** A lane is a small processor with 16-bit instructions whose timing is exact to the cycle, plus hardware assists for the bit-level work: a CRC of 5 to 32 bits, a bit stuffer, NRZI and Manchester codecs, a clock-recovery DPLL, timers and two response rules that answer inside a bit. Two protocols, or both sides of one, run at once.
- **One shared SRAM** (512 words of 32 bits) holds the programs and a data window. Each lane fetches in its own fixed time slots, so one lane can never change another lane's timing.
- **A pin crossbar** puts any lane pin on any pad, with open drain and idle levels, and lets a checking lane read pads another lane drives.
- **An event unit** timestamps events from both lanes on one clock and drives the trigger pin from a sequence of up to three stages.
- **A loader** fills the SRAM and the configuration after every power-up, with a self-test of the SRAM and a CRC-32 check, and a fallback store for the case where the SRAM self-test fails. At run time the host reaches the chip over SPI.

A lane answers a bit-level pattern and a CRC verdict at a bit offset through its two response rules, with no program in the loop. That is why the compiler can treat a target role's reaction time as a constant and prove it against the datasheet window. Where PIO or PRU would count cycles by hand, TSE places the edges and either proves the window or refuses the program.

The tile holds a UART test design (the datasheet page is `docs/info.md`); the full chip is the top `src/tt_um_tse_sk_chip.sv` with its source list in `info_chip.yaml`, and every claim below that says "on the chip" builds it.

## The instruction set

A lane runs 16-bit instructions, one source of truth in `isa/tse_isa.yaml`, from which the SystemVerilog packages and the assembler tables are generated (`make isa-gen`; CI fails on any drift). The groups:

- **Control**: `JMP` (conditional, with counter decrement), `WAIT` (stall until a pin, event or flag matches, with a timeout and an alternate condition), `DLY` (idle a fixed number of cycles).
- **Data movement**: `SET` (write an immediate), `MOV` (copy a register), `OUT` and `IN` (shift 1 to 32 bits out of or into the shift registers, for the bit-level work the assists do not), `PUSH` and `PULL` (host mailboxes), `LDW` and `STW` (load and store a data-window word in the lane's own data slot).
- **Configuration and timing**: `CSRW` and `CSRR` (write and read a lane register; configuration words take three cycles), `TMR` (timed-automaton clock control with a guard threshold), `SIG` (set chip-wide flags), `EMIT` (send a decoded event to the event unit).

Each instruction's exact encoding, cycle cost and pipeline latency (for example an output port changes two cycles after the instruction dispatches; a trigger output three cycles after `EMIT`) is in `isa/tse_isa.yaml`. The assembler and disassembler are in `tools/tseasm/`.

## Claims and evidence

The status words:

- **PROVEN**: a formal proof passes in the formal step; it must also catch a planted bug, and an UNKNOWN result counts as a failure.
- **LOCKSTEP**: the RTL matches the Python reference model instruction by instruction under randomised tests.
- **SIM**: demonstrated in the Verilator full-chip simulation and judged on the pins only by an independent decoder; no silicon, no FPGA.
- **BUILDS**: the compiler accepts or rejects the spec with a recorded, pinned outcome, checked in CI.
- **MEASURED**: a number read from the physical-design flow.
- **OPEN**: not done yet.

| Claim | Status | Run it | Caveat |
|---|---|---|---|
| A UART spec is compiled, loaded over the loader pins into the two-lane chip and run from SRAM: 46 frames sent, 30 received, 0 violations at nominal and plus or minus 1.5 percent baud | SIM | `make e2e-uart` | simulation only; one baud family |
| Every UART transmit edge lands on the cycle the compiler predicted | SIM | `make e2e-uart` | worst error about 3.5 of 10.18 ticks over 230 edges |
| I2C controller and target both run on the chip at 100 and 400 kHz, 0 judge violations; the target stretches SCL | SIM | `examples/tests/11-i2c-target.sh` | the RTL window proof for the target has not closed |
| SPI controller, modes 0 to 3, drives a simulated target | SIM, BUILDS | `examples/tests/07-spi.sh` | builds with the tile pads and with the whole pad path (`--pad-min 0 --pad-max 21 --pad-skew 3.5`); at the compiler's default 25 ns skew it is rejected (R310 on cs_n) |
| CAN receiver answers with an in-bit ACK by a response rule; a bad-CRC frame is not ACKed, at 125k and 500k | SIM | `examples/tests/09-can.sh` | receiver only; two independent CAN nodes are not expressible |
| 10BASE-T transmit, 3 frames, 0 violations | SIM | `examples/tests/10-eth10.sh` | transmit only; receive is blocked |
| SPI flash target answers an ID and a read (ef4015) | SIM | `examples/tests/08-flash.sh` | about 500 to 714 kHz only; the serprog client is a replay of flashrom's traffic, flashrom is not installed here |
| The compiler rejects a spec it cannot prove and names the fix | BUILDS | `python3 -m tools.tsec build specs/spi_target.tse` | rejected R314; with `-D t_css=142e-9 -D f_sck=1.875e6` it builds. The analysis counts the pad skew between two input pins and is replayed on the reference model under random controllers, not mechanised |
| 27 specs across 9 protocols compile to a pinned set of accept or reject outcomes | BUILDS | `make specs-test` | several are rejected by design, each for a recorded reason |
| The RTL matches the reference model instruction by instruction on random programs | LOCKSTEP | `make verif-test` | Hypothesis property tests in cocotb |
| CRC of any width 5 to 32 equals a textbook CRC; the stuffer, codec, DPLL, slot arbiter and SRAM false path meet their properties | PROVEN | `make formal` | planted bugs must fail; UNKNOWN is a failure |
| Per-program window proof on the real lane RTL | PROVEN (some obligations) | `make tsec-proof` | SPI target proved and covered; 10BASE-T transmitter obligations 1 to 3 and I2C target obligations 4 to 9 proved with rIC3; transmitter 0 and 4, I2C target 0 to 3 do not close in an hour. Every checker fails with its planted bug in simulation, so none passes vacuously |
| Check flags timing violations and sweeps a device's margin; Trigger fires on a decoded event sequence | SIM | `make check-thd-sta check-sweep check-trigger` | on the chip in simulation; compiling a spec directly into a Check program is partly done |
| Independent judges decode pins-only traces from the published standards | SIM | `make judges-test` | nine protocols |
| Unmodified OpenOCD reads a JTAG IDCODE and an SWD DPIDR, and Linux ping runs over 10BASE-T, through simulated pins | SIM | `make judges-demos judges-run` | runs on a small stand-in test top, not on the TSE lanes (the rows below are) |
| Unmodified OpenOCD finds IDCODE 0x4BA00477 through the JTAG probe lane, reads it again by DR scan and shifts BYPASS; 8 scans, 0 violations | SIM | `make demo-jtag` | the demo builds with `--pad-skew 0`; the spec also builds on the whole pad path (0/21/3.5); the TAP is a model on the pads; words of 16 pairs that OpenOCD leaves short are padded with moves that leave the TAP where OpenOCD expects it |
| Unmodified OpenOCD reads DPIDR 0x2BA01477, powers up the debug port and writes SELECT through the SWD probe lane; 19 packets, 0 violations | SIM | `make demo-swd` | SWCLK 1 MHz with 80 ns edge tolerance at `--pad-skew 0` (5 MHz is R310); runs a copy of the spec with SWDIO driven and released explicitly, and the demo clears the SWDIO pad's idle drive, two compiler gaps; the SW-DP is a model that answers OK only |
| The CAN receiver lane acknowledges 8 frames that the simulator's bit-level CAN node builds from `struct can_frame` records; 0 violations | SIM | `make demo-can` | 500 kbit/s (1 Mbit/s is R311); this WSL kernel has no vcan, so the records come over a Unix socket, not from `cansend`; receiver only |
| The Linux kernel answers an ARP request and an ICMP echo request sent by the 10BASE-T transmit lane through a TAP device | SIM | `make demo-ping` | transmit only: the replies reach the chip's receive pin, where no lane listens (R314), so ping from Linux does not complete; the frame comes from the 64-octet window variant of `eth10_tx` |
| The full two-lane chip signs off at 60 MHz: global-route overflow 0 on every layer; detailed routing, KLayout DRC, LVS, antenna, disconnected pins, max cap and max slew all 0; setup and hold met at three corners | MEASURED | `tools/pd/harden-variant.sh info_chip.yaml src/config_chip.json <tag>` | run hm2a (rtl6d with a post-route hold margin); slow-corner setup slack +1.52 ns, fast-corner hold slack +0.134 ns; Magic DRC reports 53 flags, all only on the SRAM abstract and waived (`tools/pd/magic-flags.sh`) |
| Tiny Tapeout precheck passes all nine checks | MEASURED | `tools/pd/precheck.sh <gds>` | a local run on hm2a, the full-chip layout of the signoff row (and on rtl6d and rtl5e); the official GitHub workflows have not run yet |
| Pin delays inside the tile from the clock edge: outputs 0.83 to 2.95 ns, inputs sampled 0.36 to 1.57 ns after the edge; at most 1.07 ns between two outputs and 0.85 ns between two inputs | MEASURED | `tools/pd/padskew.sh` | hm2a, over three corners (the spreads are the slow corner's); the Tiny Tapeout multiplexer and pad ring outside the tile are not included. An estimate for them, from the multiplexer's published signoff budgets and the IHP pad cell liberty, not from STA: outputs reach the bond pads at most about 19.7 ns after the tile clock edge, inputs at most about 4.5 ns, and two outputs differ by up to about 3.5 ns at the slow corner; hence `--pad-min 0 --pad-max 21 --pad-skew 3.5` |
| The routed full chip, with its signoff delays (SDF) at all three corners, loads three random images over the loader pins, commits them and reads them back exactly; in run mode it answers register reads and writes over SPI and refuses what it must while a lane runs | SIM | `make -C verif/glsdf CORNER=<corner> [GL_MODULES=test_spi GL_TEST=]` | rtl6c, `test_loader` test 1 and both `test_spi` tests; the running lane only loops on one word, so no lane drives a protocol yet; Icarus applies the delays but runs no setup or hold checks, so only a wrong value at the pins would show a timing failure; 219 wires on SRAM pins (at most 24 ps) are left out |

### Known limits

These are the compiler refusing to build a spec it cannot meet. The rejection is the evidence that the tool is honest.

- A USB low-speed device cannot drive SE0 (both wires low): the codec drives and samples one lane pin, so the two-wire line code cannot reach that state (`specs/usb_ls_device.tse`, R307).
- 10BASE-T receive is blocked by a late start-of-frame re-arm and by DPLL limits (`specs/eth10_rx.tse`, R314); transmit works.
- Two independent CAN nodes are not expressible: the shifted-in and shifted-out bits share one virtual pin (`specs/can_node.tse`, R307).
- The SPI target runs to about 1.9 MHz, not 4 MHz; the SPI flash target to about 714 kHz.
- The SPI controller specs need a pad skew bound: they build with the tile pads and with the whole pad path (`--pad-min 0 --pad-max 21 --pad-skew 3.5`), but the compiler's default of 25 ns skew between pins is rejected (R310 on cs_n).

## Verification

Four independent checks back the claims above, so a bug has to pass more than one of them:

- **Lockstep**: a Python reference model of the lane, the assists and the chip runs beside the RTL under Hypothesis-generated programs, and every instruction must agree (`make verif-test`, model in `model/`, suites in `verif/`).
- **Formal**: SymbiYosys proofs of the CRC against a textbook CRC for every width, the stuffer, the codecs, the DPLL, the slot arbiter's non-interference and the SRAM false path. Each proof must also catch a planted bug, and an UNKNOWN result is treated as a failure (`make formal`, `formal/`).
- **Independent judges**: decoders written from the published standards read the pins-only traces and count violations, so the thing that judges a run is not the thing that produced it (`make judges-test`, `verif/judges/`).
- **Compiler soundness**: every accepted or rejected build outcome is pinned and replayed against the reference model; three soundness holes were found this way and fixed with failing tests written first (`make specs-test`).

What is not covered, stated plainly: no silicon, no FPGA, no measured CMOS5L pad timing; the compiler's timing analysis is checked against the reference model but is not a mechanised proof; several per-program RTL window proofs do not close yet; the full chip's signoff and precheck are local runs, as the official GitHub workflows have not run yet; the pad delays outside the tile are estimates; and at gate level no lane drives a protocol on its pins yet. `make regress` runs the standing suite end to end.

## What we do differently from PIO and PRU

1. **Timing is checked before a program exists.** With PIO or PRU the programmer counts cycles; here the compiler places every edge and rejects a spec it cannot meet. Example: `specs/spi_target.tse` at 4 MHz is rejected (`R314`, the lane reaches its wait for the next SCK rise too late, fix `f_sck <= 1.99 MHz`); with `-D t_css=142e-9 -D f_sck=1.875e6` it builds.
2. **Protocol assists in every lane.** CRC, bit stuffing, line codes, clock recovery and response rules are hardware, so a lane does not spend instructions per bit on them (PIO has none of these; a PRU does them in software).
3. **Lanes cannot disturb each other.** Memory access is granted by fixed slots, proven with SymbiYosys for the slot arbiter (`formal/arb/`), and measured end to end in `examples/combo/`.

PIO is much smaller and mature, and a PRU is a full CPU; TSE trades both for timing that is stated and checked.

## Related work

Other public projects reached parts of the same idea first. This is an honest credit; none of their code is used here.

- **SPEX** runs timed-automaton protocol specifications in Drive, Check, Stress and Cover roles and rejects programs it cannot meet. It is the closest conceptual neighbour and made the earliest public claim of a chip that executes timed-automaton protocol specs.
- **Escapement** proves, unbounded and in hardware, that no pin edge is ever late for any program, and refuses firmware that is not certified. Its guarantee is stronger in form than a per-program compiler analysis.
- **MarcosAsh** compiles a job to certified firmware or a named refusal and sweeps real device margins on a bench.
- **FIPE** compiles timing rules out of a spec into on-chip timing monitors and publishes measured device datasheets, on a closed layout.

What is left that is TSE's, all on one design: reaction-window proofs for target roles over every memory slot phase with the fix named; hardware response rules that answer a bit pattern plus a CRC verdict at a bit offset; a CRC of any width 5 to 32 proved equal to a textbook CRC; and Speak, Check and a three-stage decoded-event Trigger from one spec on the same chip. The honest counterweight: TSE's layout signs off and passes the precheck only in local runs, and there is no hardware, while most serious rivals have a public passing GDS, hardware, or both.

## Run it

Everything runs on Linux (Ubuntu 22.04 or 24.04, or WSL2). Needed: the OSS CAD Suite (Verilator, Icarus Verilog, Yosys, SymbiYosys), `g++`, `make`, Python 3.11 with cocotb 2.1, pytest, Hypothesis and PyYAML. The Tiny Tapeout template test in `test/` pins its own versions in `test/requirements.txt`.

All tests, one step after the other, with a log per step in `build/regress/` and a summary table at the end (about 15 minutes):

    make regress

The end-to-end examples (the first run builds the Verilated chip, about 2 minutes):

    make e2e-test            # all examples and their checks
    examples/uart/run.sh     # one example; outputs in build/e2e/uart/
    examples/i2c/run.sh

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
