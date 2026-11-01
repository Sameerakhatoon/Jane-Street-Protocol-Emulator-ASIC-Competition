#!/usr/bin/env bash
# Every chip latch is the reset latch cell in the CMOS5L flow: elaborate
# tse_chip with the flow's library define (the cell as a black box) and with
# the behavioural latches, and require no generic latch left in the first and
# as many cells as the second has latch bits.
cd "$(dirname "$0")/../.."
out=build/rtl-sim/latch-cells; mkdir -p $out
src=$(sed -n '/^ *source_files:/,/^[^ ]/s/^ *- *"\([^"]*\)".*/src\/\1/p' info_chip.yaml | grep -v -e _bb.v -e tt_um_ | tr '\n' ' ')
cat > $out/cell_bb.v <<'EOF'
(* blackbox *) module sg13cmos5l_dlhrq_1 (output Q, input D, input GATE, input RESET_B); endmodule
EOF

count() {   # count <tag> <defines>: generic latch bits and reset latch cells after elaboration
    yosys -q -l $out/$1.log -p "read_verilog -lib $out/cell_bb.v; read_verilog -lib src/tse_sram_bb.v; \
        read_verilog -sv $2 $src; hierarchy -top tse_chip -chparam LANES 2; proc; flatten; opt; \
        simplemap; opt_clean; tee -q -o $out/$1.stat stat" > /dev/null 2>&1 || { echo "FAIL latch-cells (yosys $1)"; return 1; }
    lat=$(grep -E '\$_DLATCH' $out/$1.stat | awk '{s += $1} END {print s + 0}')
    cel=$(grep -E 'sg13cmos5l_dlhrq_1' $out/$1.stat | awk '{s += $1} END {print s + 0}')
}

count beh "" || exit 1
beh_lat=$lat
count cell "-DSCL_sg13cmos5l_stdcell" || exit 1
if [ "$beh_lat" -gt 0 ] && [ "$lat" -eq 0 ] && [ "$cel" -eq "$beh_lat" ]; then
    echo "PASS latch-cells ($cel cells for $beh_lat latch bits)"
else
    echo "FAIL latch-cells: behavioural $beh_lat latch bits; with the define $lat latch bits and $cel cells"
    exit 1
fi
