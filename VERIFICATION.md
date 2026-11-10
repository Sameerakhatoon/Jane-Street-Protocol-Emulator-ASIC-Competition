# How TSE is verified

TSE makes one promise: if the compiler accepts a spec, every edge the chip drives lands inside the window the datasheet allows. If it cannot keep that promise, it refuses to build. Everything below exists to check that promise, and to check the checkers.

No chip exists yet. All evidence is simulation, formal proof or numbers from the physical-design flow. Where something is not covered, this page says so.

## The idea: never let one thing grade its own work

A bug that slips past one check usually slips past it because the check shares the bug's assumptions. So TSE uses five checks that are built independently and disagree loudly:

| Check | What it compares | Where |
|---|---|---|
| Lockstep | the RTL against a Python model of the lane, instruction by instruction | `model/`, `verif/`, `make verif-test` |
| Formal proofs of the blocks | each hardware assist against its definition | `formal/`, `make formal` |
| Per-program proofs | one compiled program on the real lane RTL against its timing windows | `tools/tsec/proofs/`, `make tsec-proof`, `make tsec-proof-more` |
| Independent judges | pin traces against the published protocol standards | `verif/judges/`, `make judges-test` |
| Pinned compiler outcomes | every spec's accept or reject decision against a recorded result | `specs/tests/`, `make specs-test` |

`make regress` runs all of it in one go, about 15 minutes, with a log per step.

## 1. Lockstep: the RTL against a model

The reference model in `model/` is a plain Python description of a lane, its assists and the whole chip: what each instruction does, in how many cycles. Hypothesis generates random programs, and cocotb runs each one on the RTL and on the model side by side. After every instruction, registers, pins and memory must match.

This catches the ordinary bugs: a wrong flag, an off-by-one cycle count, a pipeline hazard. It cannot catch a bug that the model and the RTL share, which is why the model is not the only reference.

## 2. Formal proofs of the hardware blocks

SymbiYosys proves the parts that must be right for every input, not just for the inputs a test happened to try:

- the CRC engine equals a textbook CRC for every width from 5 to 32 bits;
- the bit stuffer, the NRZI and Manchester codecs and the clock-recovery DPLL meet their properties;
- the memory slot arbiter gives each lane its own fixed slots, so one lane can never change another lane's timing;
- the SRAM read data never steers the SRAM inputs in the same cycle (a false path the timing flow relies on).

Two rules keep the proofs honest. Every proof comes with a planted bug that must make it fail; a proof that also passes with the bug is proving nothing. And an UNKNOWN result from the solver counts as a failure, never as a pass.

Mutation testing (`formal/mcy/`) goes one step further for the CRC, stuffer, codec and response rules: it changes the RTL in many small ways and checks that the tests notice each change.

## 3. Per-program proofs: one program, the real RTL, its windows

The compiler's timing analysis is the heart of TSE, so its result is checked a second way. For a compiled program, `tsec` writes a proof harness: the real lane RTL running that program, plus one small checker per timing window ("this response must follow that trigger within lo to hi cycles"). A model checker (abc pdr or rIC3) then proves no input sequence can break a window.

Each window is proved alone, and each comes with a planted bug: the same checker with a window no response can meet, which must fail. That shows the trigger and the response are actually reached, so the proof is not vacuous.

Proved this way: the SPI target; SPI flash target obligations 0 to 9; the JTAG probe; the I2C controller at 400 kHz, 0 to 29; the I2C target, 4 to 9; the 10BASE-T transmitter, 1 to 3; the CAN receiver, 0 to 7; the SPI controller in mode 0, 0 to 10; and the UART transmitter and receiver, 0 and 1. The I2C target's obligations 0 to 3 and the transmitter's 0 and 4 did not close within an hour; they are shown non-vacuous in simulation only.

Two techniques made the hard ones close:

- **Cut engines.** For the CAN receiver, the lane's assists are replaced by free inputs. Every behaviour of the real engines is one of the behaviours of a free input, so a proof that holds here holds for the real lane.
- **Lemmas, always asserted.** For the long UART and SPI chains, the solver gets helper facts mined from simulation, such as "this counter plus that delay stays within these bounds". Each lemma is asserted next to the obligation, never assumed, so a wrong lemma makes the proof fail instead of making it pass.

A few planted bugs are only reached thousands of cycles in, too deep for the solver within an hour. Those are shown failing in simulation of the same harness instead, and the table in the README says which.

## 4. Independent judges

The chip's own logs are not trusted to grade the chip. For each protocol there is a decoder in `verif/judges/`, written from the published standard and not from TSE's code. It reads only the pin traces of a run and counts every violation of the standard's timing and framing.

Nine protocols have judges. The end-to-end examples (UART, SPI, I2C controller and target, SPI flash, CAN, JTAG, SWD, 10BASE-T) all finish with zero judge violations.

Some runs go further and talk to real, unmodified software through simulated pins: OpenOCD reads a JTAG IDCODE and an SWD DPIDR through the chip's probe lanes, and the Linux kernel answers ARP and ping sent by the 10BASE-T transmit lane through a TAP device.

## 5. The compiler checked against itself

Every spec in `specs/` has a recorded outcome: accepted, or rejected with a specific reason code and a suggested fix. CI rebuilds them all and fails on any change. Accepted programs are then replayed on the reference model under randomised partners, to check that the edges land where the compiler said they would. Three soundness holes in the timing analysis were found this way; each was fixed only after a failing test reproduced it.

The rejections are evidence too. When the compiler refuses a spec (USB low-speed SE0, 10BASE-T receive, SPI target at 4 MHz), it names the rule that cannot be met and by how much. A tool that never refuses is not checking anything.

## 6. After layout

The full chip goes through the Tiny Tapeout flow and the precheck.

The 8x4 full chip hardens and signs off at 60 MHz in the official Tiny Tapeout GitHub workflow (`gds` run 42834673499, commit 8cb3d4e):

- Detailed routing finishes with 0 violations.
- Setup slack: +1.61 ns at the slow corner, +4.30 ns typical, +4.22 ns fast.
- Hold slack: +0.146 ns at the fast corner, +0.29 ns typical, +0.53 ns slow.
- Magic DRC 0, the IHP KLayout sign-off DRC deck 0, LVS 0, antenna 0 (21 diodes added), max slew 0, max capacitance 0, disconnected pins 0.
- Standard-cell utilisation about 40 percent, 29,153 cells.
- Magic checks the SRAM macros through the abstract view in `src/maglef`; the vendor SRAM layout itself is checked by the KLayout deck.
- The Tiny Tapeout precheck passes all ten checks, and the template gate-level test passes on the routed netlist.

The pin delays below and the SDF simulation come from the local run t8x4b, an earlier layout of the same RTL:

- Pin delays inside the tile from the clock edge (slow corner): outputs 1.93 to 3.12 ns, inputs sampled 0.74 to 1.57 ns after the edge, at most 1.19 ns between two outputs and 0.82 ns between two inputs. The multiplexer and pad ring outside the tile are estimated, not measured.

The routed netlist is simulated with its signoff delays (SDF) at all three corners: it loads random images over the loader pins, reads them back exactly, and answers register reads and writes over SPI.

## What is not covered

- No silicon and no FPGA run; the pad delays outside the tile are estimates, not measurements.
- The compiler's timing analysis is checked by replay and by the per-program proofs, but the analysis itself is not a mechanised proof.
- Per-program proofs cover the obligations listed above, not every spec in `specs/`.
- At gate level, no lane drives a full protocol on its pins yet.
