# Shared helper for the numbered SystemVerilog tests: compile one testbench
# against every file in src/ with the SRAM simulation model, run it, and pass
# only if it prints "PASS <tb>" and no FAIL line.
cd "$(dirname "$0")/../.."
mkdir -p build/rtl-sim

run_tb() {   # run_tb <tb> [iverilog args]
    local tb=$1; shift
    local out=build/rtl-sim/$tb
    if ! iverilog -g2012 -DSIM "$@" -o $out.vvp -s $tb test/rtl/$tb.sv $(ls src/*_pkg.sv 2>/dev/null) $(ls src/*.sv | grep -v _pkg.sv) src/*.v \
            > $out.build.txt 2>&1; then
        echo "FAIL $tb (build)"; grep -v sorry $out.build.txt | head -20; return 1
    fi
    timeout 900 vvp -n $out.vvp > $out.log 2>&1
    if grep -q "^PASS $tb" $out.log && ! grep -q FAIL $out.log; then
        echo "PASS $tb $*"
    else
        echo "FAIL $tb $*"; grep -E "FAIL|ERROR|Error" $out.log | head -20; return 1
    fi
}
