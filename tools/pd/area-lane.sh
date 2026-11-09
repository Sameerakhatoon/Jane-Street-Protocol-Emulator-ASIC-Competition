#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Area of the lane blocks and of the full chip (2 lanes, latch config):
# the quick check after a lane change. Same flow and file order as area-all.sh.
set -eu
cd "$(dirname "$0")/../.."
A="bash tools/pd/area.sh"
s() { printf 'src/%s ' "$@"; }
pkg="tse_isa_pkg.sv tse_regs_pkg.sv"
lane="tse_lane.sv tse_seq.sv tse_crc.sv tse_stuffer.sv tse_codec.sv tse_dpll.sv tse_resp_rule.sv tse_timer.sv tse_nco.sv"

$A tse_seq "" $(s $pkg tse_seq.sv)
$A tse_resp_rule "" $(s tse_resp_rule.sv)
$A tse_lane "" $(s $pkg $lane)
$A tse_chip "-chparam CFG_LATCH 1" $(s $pkg tse_chip.sv tse_top.sv tse_rst_sync.sv tse_cfg_bank.sv \
    $lane tse_xbar.sv tse_pin_delay.sv tse_prog_mem.sv tse_slot_arb.sv tse_sram_512x32.sv tse_loader.sv tse_selftest.sv \
    tse_event_unit.sv tse_host_spi.sv tse_sram_bb.v)
