#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Lint gate: every module in src/ as its own top with verilator -Wall and no
# waivers, the Tiny Tapeout top and tse_chip (latch and flop configuration)
# through iverilog too. Prints one line per check, fails on any warning.
set -u
cd "$(dirname "$0")/../.."

top=$(sed -n 's/^ *top_module: *"\([^"]*\)".*/\1/p' info.yaml)
# Packages first so modules can import them; they are not linted as tops.
rtl="$(ls src/*_pkg.sv 2>/dev/null) $(ls src/*.sv | grep -v _pkg.sv)"
bb="$(ls src/*_bb.v 2>/dev/null || true) test/rtl/sg13cmos5l_antennanp.v"
log=build/lint; mkdir -p $log
fail=0

vl() {   # vl <name> <top> [verilator args]
    local name=$1 t=$2; shift 2
    if verilator --lint-only -Wall --top-module $t "$@" $rtl $bb > $log/$name.txt 2>&1; then
        echo "lint ok      $name"
    else
        echo "LINT FAIL    $name"; grep -E '^%' $log/$name.txt | head -20; fail=1
    fi
}

iv() {   # iv <name> <top> [iverilog args]
    local name=$1 t=$2; shift 2
    if iverilog -g2012 -o /dev/null -s $t "$@" $rtl $bb > $log/iv_$name.txt 2>&1; then
        echo "iverilog ok  $name"
    else
        echo "IVERILOG FAIL $name"; grep -v sorry $log/iv_$name.txt | head -20; fail=1
    fi
}

for f in $rtl; do
    m=$(basename $f .sv)
    case $m in tse_chip|*_pkg) continue;; esac
    vl $m $m
done
iv $top $top
[ -f src/tse_cfg_word.sv ] && vl tse_cfg_word_flop tse_cfg_word -GLATCH=0

if [ -f src/tse_chip.sv ]; then
    for C in 1 0; do
        vl tse_chip_C$C tse_chip -GCFG_LATCH=$C
        iv tse_chip_C$C tse_chip -Ptse_chip.CFG_LATCH=$C
    done
fi

if [ -f src/tse_sram_model.v ]; then
    for m in $(sed -n 's/^module \([A-Za-z0-9_]*\).*/\1/p' src/tse_sram_model.v); do
        if verilator --lint-only -Wall -DSIM --top-module $m src/tse_sram_model.v > $log/sram_model_$m.txt 2>&1; then
            echo "lint ok      $m (SIM model)"
        else
            echo "LINT FAIL    $m (SIM model)"; grep -E '^%' $log/sram_model_$m.txt | head; fail=1
        fi
    done
fi
exit $fail
