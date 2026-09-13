# Tests of the UART test design through the Tiny Tapeout pins only, so the same file runs
# on the gate-level netlist. Keep to the cocotb 2.0.1 API: the template CI
# job pins that version.
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, Timer

CLK_PS = 16666                       # 60 MHz
BIT_PS = round(1e12 / 115200)
TX_PIN, RX_PIN = 0, 3                # uo_out[0], ui_in[3]
BUSY, RX_TOG, RX_ERR = 1, 2, 3       # uo_out bits
ECHO = 0x02                          # uio_in[1]


def pin(sig, n):
    return (int(sig.value) >> n) & 1


async def reset(dut, uio=0):
    cocotb.start_soon(Clock(dut.clk, CLK_PS, unit="ps").start())
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = uio
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 10)


async def uart_decode(dut):
    """decode one 8N1 byte from uo_out[0], sampling in the middle of each bit."""
    await FallingEdge(dut.uart_tx)
    await Timer(BIT_PS // 2, unit="ps")
    assert dut.uart_tx.value == 0, "start bit not low at mid-bit"
    value = 0
    for i in range(8):
        await Timer(BIT_PS, unit="ps")
        value |= int(dut.uart_tx.value) << i
    await Timer(BIT_PS, unit="ps")
    assert dut.uart_tx.value == 1, "stop bit not high"
    return value


async def uart_send(dut, byte, stop=1):
    """Drive one 8N1 frame into ui_in[3]."""
    for bit in [0] + [(byte >> i) & 1 for i in range(8)] + [stop]:
        dut.ui_in.value = bit << RX_PIN
        await Timer(BIT_PS, unit="ps")
    dut.ui_in.value = 1 << RX_PIN


@cocotb.test()
async def test_01_tx_idle_high(dut):
    await reset(dut)
    for _ in range(100):
        await ClockCycles(dut.clk, 50)
        assert pin(dut.uo_out, TX_PIN) == 1, "TX not idle high"
        assert pin(dut.uo_out, BUSY) == 0, "busy without a send"


@cocotb.test()
async def test_02_tx_strobe(dut):
    await reset(dut)
    for byte in (0x55, 0xA3, 0x00, 0xFF, 0x0F):
        dut.ui_in.value = byte
        await ClockCycles(dut.clk, 5)
        rx = cocotb.start_soon(uart_decode(dut))
        dut.uio_in.value = 1
        await ClockCycles(dut.clk, 4)
        dut.uio_in.value = 0
        got = await rx
        assert got == byte, f"sent {byte:#04x}, decoded {got:#04x}"
        while pin(dut.uo_out, BUSY):
            await ClockCycles(dut.clk, 16)


@cocotb.test()
async def test_03_rx_echo_all_bytes(dut):
    await reset(dut, uio=ECHO)
    dut.ui_in.value = 1 << RX_PIN
    await ClockCycles(dut.clk, 100)
    tog = pin(dut.uo_out, RX_TOG)
    echoed = []

    async def collect():
        for _ in range(256):
            echoed.append(await uart_decode(dut))

    collector = cocotb.start_soon(collect())
    # One idle bit between frames: the echo of each byte overlaps the next
    # frame, and the gap absorbs the transmitter's wait for its first tick.
    for byte in range(256):
        await uart_send(dut, byte)
        await Timer(BIT_PS, unit="ps")
        assert pin(dut.uo_out, RX_ERR) == 0, f"framing error on {byte:#04x}"
    await collector
    assert echoed == list(range(256)), f"echo mismatch at {[i for i, b in enumerate(echoed) if b != i][:4]}"
    assert pin(dut.uo_out, RX_TOG) == tog, "RX toggle count is not even after 256 bytes"


@cocotb.test()
async def test_04_rx_framing_error(dut):
    await reset(dut, uio=ECHO)
    dut.ui_in.value = 1 << RX_PIN
    await ClockCycles(dut.clk, 100)
    tog = pin(dut.uo_out, RX_TOG)
    await uart_send(dut, 0x3C, stop=0)
    await Timer(BIT_PS, unit="ps")
    assert pin(dut.uo_out, RX_ERR) == 1, "framing error not flagged"
    assert pin(dut.uo_out, RX_TOG) == tog, "bad frame counted as a byte"
    assert pin(dut.uo_out, BUSY) == 0, "bad frame was echoed"
    echo = cocotb.start_soon(uart_decode(dut))
    await uart_send(dut, 0xC3)
    assert await echo == 0xC3
    assert pin(dut.uo_out, RX_ERR) == 0, "framing error not cleared by a good byte"
