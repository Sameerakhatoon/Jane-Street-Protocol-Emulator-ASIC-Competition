#!/usr/bin/env bash
# Lint gate: every module in src/ as its own top with verilator -Wall and no
# waivers, the Tiny Tapeout top and tse_chip (3 and 4 lanes, latch and flop
# config) through iverilog too. Prints one line per check, fails on any warning.
set -u
cd "$(dirname "$0")/../.."

top=$(sed -n 's/^ *top_module: *"\([^"]*\)".*/\1/p' info.yaml)
# Packages first so modules can import them; they are not linted as tops.
rtl="$(ls src/*_pkg.sv 2>/dev/null) $(ls src/*.sv | grep -v _pkg.sv)"
bb=$(ls src/*_bb.v 2>/dev/null || true)
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
[ -f src/tse_slot_arb.sv ] && vl tse_slot_arb_L4 tse_slot_arb -GLANES=4
[ -f src/tse_cfg_word.sv ] && vl tse_cfg_word_flop tse_cfg_word -GLATCH=0

if [ -f src/tse_chip.sv ]; then
    for L in 3 4; do for C in 1 0; do
        vl tse_chip_L${L}_C$C tse_chip -GLANES=$L -GCFG_LATCH=$C
        iv tse_chip_L${L}_C$C tse_chip -Ptse_chip.LANES=$L -Ptse_chip.CFG_LATCH=$C
    done; done
fi

if [ -f src/tse_sram_model.v ]; then
    if verilator --lint-only -Wall -DSIM src/tse_sram_model.v > $log/sram_model.txt 2>&1; then
        echo "lint ok      tse_sram_model (SIM)"
    else
        echo "LINT FAIL    tse_sram_model"; grep -E '^%' $log/sram_model.txt | head; fail=1
    fi
fi
exit $fail
