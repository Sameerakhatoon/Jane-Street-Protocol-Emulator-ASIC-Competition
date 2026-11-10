# TSE toolchain

This page takes you from a clean Linux machine to a compiled protocol, a load image and a run in simulation. Every command below runs from the repository root.

| Tool | Where | What it does |
|---|---|---|
| `tsec` | `tools/tsec/` | the timed-spec compiler: spec in, lane programs, configuration and a timing report out, or a rejection with the reason and a fix |
| `tseasm` | `tools/tseasm/` | assembler and disassembler for hand-written lane programs |
| host library and `tse_cli` | `host/tse_host/`, `host/tse_cli.py` | build load images, load and verify them, read and write registers |
| board code | `host/tse_rp2/` | the same loader in MicroPython for the RP2350 on the Tiny Tapeout demo board |
| reference model | `model/` | a Python model of the lane and the chip; `tse_cli` uses it when no chip is attached |
| simulator | `sim/` | the Verilated full chip, with bridges to real software (pty, TAP, OpenOCD, CAN, serprog) |
| judges | `verif/judges/` | decoders written from the protocol standards that check pins-only traces |

The instruction set and register map are in [isa.md](isa.md), and the hardware in [architecture.md](architecture.md).

## 1. Install

Linux (Ubuntu 22.04 or 24.04) or WSL2.

**The compiler, assembler and host CLI** need only Python 3.11 or newer and PyYAML:

    sudo apt install python3 python3-yaml

**The simulations and tests** also need the OSS CAD Suite (Verilator, Icarus Verilog, Yosys, SymbiYosys) on `PATH`, `g++` and `make`, and cocotb 2.1, pytest and Hypothesis:

    sudo apt install build-essential
    pip install cocotb==2.1.0 pytest hypothesis pyyaml

The Tiny Tapeout template test in `test/` pins its own versions in `test/requirements.txt`.

## 2. Compile a spec (about a minute)

    python3 -m tools.tsec build specs/uart_tx.tse -o build/uart_tx --emit all

`tsec` parses the spec, places every edge and sample cycle by cycle, checks each against its window, and only then writes files:

| File | Contents |
|---|---|
| `uart_tx.img` | the lane program, one 16-bit word per line |
| `uart_tx.lst` | the listing: address, word, instruction and why it is there |
| `uart_tx.report.txt` | every timing obligation with its ideal time, the placed time and the margin |
| `uart_tx.json` | programs, configuration registers and pin plan, for the image builder |
| `uart_tx.lir` | the lowered intermediate form |
| `uart_tx_<role>.xml` | an UPPAAL timed-automaton model of the role |
| `proof/` | the per-program proof harness for the formal tools (`make tsec-proof`) |

`--emit` takes any comma list of `img, lst, report, json, lir, xml, proof`, or `all`. Parameters can be changed without editing the spec:

    python3 -m tools.tsec build specs/uart_tx.tse -D baud=1000000 --fclk 50e6 -o build/uart_1m

Now a spec the chip cannot meet:

    python3 -m tools.tsec build specs/spi_target.tse

It exits with status 2 and writes nothing. The diagnostic names the rule (R311: the first MOSI sample of the next byte comes too late), the cycle path, how late the edge is (53.3 ns) and a fix (`f_sck <= 2.8 MHz`). With the fix applied it builds:

    python3 -m tools.tsec build specs/spi_target.tse -D t_css=142e-9 -D f_sck=3.3e6 -o build/spi_target

`python3 -m tools.tsec check <spec>` only parses and checks a spec; `fmt` prints it in canonical form.

### Pad delays

The compiler assumes pad delays of 0 to 25 ns and up to 25 ns between two pins unless told otherwise. With the tile's measured pads, or the whole path through the Tiny Tapeout multiplexer and the IHP pads (estimated), more specs build:

    python3 -m tools.tsec build specs/spi_controller_mode0.tse --pad-min 0 --pad-max 21 --pad-skew 3.5

Separate input and output ranges are `--pad-in-min/max/skew` and `--pad-out-min/max/skew`.

### Rejection codes

| Code | Meaning |
|---|---|
| R300 | a language feature this version does not lower yet |
| R301 | program too large (more than 256 instructions, or a jump beyond them) |
| R303 | more response rules than a lane has (two) |
| R304 | the role needs more assists than a lane has, or an assist lacks the event asked for |
| R306 | more roles than lanes (two) |
| R307 | pin plan impossible with a lane's 4 virtual pins and its assists |
| R308 | a frame cannot be laid out as written |
| R310 | an output edge cannot be placed in its window |
| R311 | a response cannot follow its trigger in time |
| R312 | an input cannot be sampled in its window |
| R313 | a level cannot be held as long as required |
| R314 | a wait starts after the edge it must catch may already be visible |
| R316 | the program cannot feed an assist its next data in time |
| R317 | the program cannot keep up with a bit timer |
| R318, R319 | a response rule's position or delay is out of range |

`specs/tests/expected-outcomes.txt` pins the outcome of every spec and argument set; `make specs-test` fails on any change.

## 3. The spec language

A spec describes one side of a protocol as the datasheet does. `specs/uart_tx.tse`, without its comments:

```
protocol uart_tx {
  param baud : freq  = 115200 Hz;
  param tol  : ratio = 2 %;
  unit  bit  = 1 / baud;

  wire tx : out push_pull idle 1;

  role tx_side : controller uses tx {
    var b : bits[8];
    state idle  { on host.pull(b) -> frame; }
    state frame {
      seq {
        drive tx = 0 for 1 bit ~ tol;            // start bit
        shift b lsb 8 -> tx each 1 bit ~ tol;    // data, least significant first
        drive tx = 1 for 1 bit ~ tol;            // stop bit
      } -> idle;
    }
  }
}
```

- **`param`** values carry units (`Hz`, `kHz`, `ns`, `%`) and can be overridden with `-D`. **`unit`** names a duration derived from them.
- **`wire`** declares a pin with its direction, drive (`push_pull`, `open_drain`, `pull_up`) and idle level.
- **`role`** is one side of the protocol (`controller` or `target`); a spec may have several, and each takes one lane.
- **`state`** blocks form the role's state machine; `on <event> -> <state>` moves between them. Events include pin edges (`rise(x)`, `fall(x)`), the host mailbox (`host.pull(b)`) and assist verdicts (`crc15.ok`); `push host f` in a `seq` hands data back to the host.
- **`seq`** lists timed actions. `drive tx = 0 for 1 bit ~ tol` is an output with its duration and tolerance; `shift ... each 1 bit` sends or receives bits at a fixed rate. Every `~ tol` becomes a timing obligation in the report.

Larger protocols use assists, frames and response rules. From `specs/can_rx_ack.tse`:

```
  assist crc15 : crc     { width = 15; poly = 0x4599; init = 0; refin = false;
                           refout = false; xorout = 0; check = 0x059E; }
  assist stuff : stuffer { run = 5; value = either; }
  assist bt    : dpll    { period = 1 bit; sample = 80 %; sjw = 4 tq; dual_edge = true; edges = fall; }

  frame can_std {
    sof : bit = 0;  id : bits[11];  rtr : bit;  ide : bit = 0;  r0 : bit = 0;
    dlc : uint[4];  data : bytes[0..8];
    crc : bits[15] crc crc15 over sof..data;
    crc_del : bit = 1 nostuff;  ack : bit = 1 nostuff from node;
    ack_del : bit = 1 nostuff;  eof : bits[7] = 0x7F nostuff;
  }
  ...
    state ack_slot {
      respond ack : on crc15.ok { drive txd = 0 for 1 bit; } within [0 ns, 1 tq] -> deliver;
      on crc15.bad -> idle;
    }
```

- **`assist`** configures a lane assist: `crc`, `stuffer`, `nrzi`, `manchester`, `dpll`, `timer`, `rule` or `arbitration`.
- **`frame`** lays out a packet field by field; `crc ... over sof..data` computes and checks the CRC in hardware, `nostuff` excludes a field from bit stuffing.
- **`respond ... within [lo, hi]`** is a reaction with a deadline. The compiler maps it to a response rule and proves the window, or rejects the spec with R311.
- **`datasheet`** rules (`fall(rxd) -> rise(rxd) >= 1 bit`) state timing the bus must obey; Check mode reports violations of them.

The `specs/` folder has 27 specs across UART, SPI, I2C, SPI flash, JTAG, SWD, CAN, 10BASE-T, PS/2 and USB low speed to copy from.

## 4. Assemble by hand

Most programs come from `tsec`, but a lane can also be programmed directly. One instruction per line, pioasm style:

```
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
        JMP ALWAYS, frame
```

`[n]` adds n delay cycles, `side n` sets the side-set pins. Directives: `.program`, `.window`, `.start`, `.side_set`, `.define`, `.align`, `.word`, `.config REG value`.

    python3 -m tools.tseasm tools/tseasm/tests/uart_tx.s -o build/uart_tx.hex -l build/uart_tx.lst
    python3 -m tools.tseasm -d build/uart_tx.hex          # disassemble

## 5. Build an image and load it

A load image (`.tsei`) holds the programs, the lane configuration and the crossbar settings, with a CRC.

    python3 host/tse_cli.py image build -o build/uart_tx.tsei --lane 0=tools/tseasm/tests/uart_tx.s --chip PAD_OUT.0=0x40
    python3 host/tse_cli.py image show build/uart_tx.tsei

`PAD_OUT.0=0x40` enables pad `uo_out[0]` with lane 0's virtual pin 0 as its source (fields in [isa.md](isa.md)). The examples build images from `tsec` output in the same way, choosing the pads for each wire (`examples/tse_demo.py`).

Without a chip, `tse_cli` loads into the reference model, keeping its state in a file:

    python3 host/tse_cli.py --state build/model.json load build/uart_tx.tsei --run 1
    python3 host/tse_cli.py --state build/model.json status

`load` runs the full sequence the chip needs after every power-up: BEGIN, the SRAM self-test, the writes, COMMIT with the CRC, STAT and a readback of every word. `peek` and `poke` read and write single registers.

On the demo board, the same steps run in MicroPython from `host/tse_rp2/`; see "How to test" in [info.md](info.md).

## 6. Run it in simulation

Each example compiles its spec, builds the image, loads it over the loader pins into the Verilated full chip, runs it against a peer and has an independent judge check the pin trace:

    examples/uart/run.sh          # outputs in build/e2e/uart/
    examples/i2c/run.sh
    make e2e-test                 # every example

The first run builds the simulator, about 2 minutes. `make demos` runs the demos with real software (OpenOCD over JTAG and SWD, Linux over a TAP device, a CAN socket, flashrom's serprog traffic). `make regress` runs every test in the repository with a log per step, about 15 minutes.

## 7. Add a protocol

A new protocol needs no change to the chip:

1. Copy the closest spec in `specs/` and describe the new protocol's wires, states, timing and frame from its datasheet.
2. `python3 -m tools.tsec build` it. If it is rejected, the diagnostic names the rule, how far it misses and a fix; change a parameter or the spec, or accept that the chip cannot meet it.
3. Read `<name>.report.txt` to see every obligation and its margin.
4. Run it in simulation (copy an `examples/` folder), and judge the trace with a decoder from `verif/judges/` or a new one written from the standard.
5. Optionally prove the timing windows on the lane RTL with the generated `proof/` harness (`make tsec-proof` shows how).
6. Add its outcome to `specs/tests/expected-outcomes.txt` so later compiler changes cannot silently change it.
