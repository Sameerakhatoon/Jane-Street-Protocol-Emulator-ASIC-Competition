#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Area of the calibration block, the Tiny Tapeout top, every chip block and
# the whole chip with latch and with flop configuration. File lists are fixed and in a
# fixed order because ABC's result moves by a few tenths of a percent when
# Yosys reads extra or reordered files.
set -eu
cd "$(dirname "$0")/../.."
A="bash tools/pd/area.sh"
s() { printf 'src/%s ' "$@"; }

# CAL_CRC: optional path to the original crc_unit.sv, the calibration block
# (18290.55 um^2 when the flow and Liberty match).
if [ -n "${CAL_CRC:-}" ]; then $A crc_unit "" "$CAL_CRC"; fi

top=$(sed -n 's/^ *top_module: *"\([^"]*\)".*/\1/p' info.yaml)
$A $top "" $(sed -n '/^ *source_files:/,/^[^ ]/s/^ *- *"\([^"]*\)".*/src\/\1/p' info.yaml)

lane="tse_lane.sv tse_seq.sv tse_crc.sv tse_stuffer.sv tse_codec.sv tse_dpll.sv tse_resp_rule.sv tse_timer.sv tse_nco.sv"
$A tse_seq "" $(s tse_isa_pkg.sv tse_regs_pkg.sv tse_seq.sv)
for b in tse_crc tse_stuffer tse_codec tse_dpll tse_resp_rule tse_xbar tse_pin_delay \
         tse_cfg_bank tse_event_unit tse_loader tse_host_spi tse_selftest; do
    $A $b "" $(s $b.sv)
done
$A tse_timer "" $(s tse_timer.sv tse_nco.sv)
$A tse_prog_mem "" $(s tse_prog_mem.sv tse_slot_arb.sv tse_cfg_bank.sv tse_sram_512x32.sv tse_sram_bb.v)
$A tse_lane "" $(s tse_isa_pkg.sv tse_regs_pkg.sv $lane)

chip=$(s tse_isa_pkg.sv tse_regs_pkg.sv tse_chip.sv tse_top.sv tse_rst_sync.sv tse_cfg_bank.sv $lane tse_xbar.sv tse_pin_delay.sv \
         tse_prog_mem.sv tse_slot_arb.sv tse_sram_512x32.sv tse_loader.sv tse_selftest.sv tse_event_unit.sv \
         tse_host_spi.sv tse_sram_bb.v)
for c in 1 0; do
    $A tse_chip "-chparam CFG_LATCH $c" $chip
done
