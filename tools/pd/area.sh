#!/usr/bin/env bash
# Map one block onto CMOS5L standard cells with Yosys and print its area:
# typical corner, before placement, SRAM as a black box, so placement and
# routing overhead come on top of these numbers.
# usage: tools/pd/area.sh <top> "<hierarchy -chparam args>" <file>...
# The Liberty file is $LIB, else build/lib/<name>, copied once from $LIB_SRC
# (a directory holding the CMOS5L typical-corner standard-cell Liberty).
set -eu
cd "$(dirname "$0")/../.."

LIB_NAME=sg13cmos5l_stdcell_typ_1p20V_25C.lib
if [ -z "${LIB:-}" ]; then
    LIB=build/lib/$LIB_NAME
    if [ ! -f $LIB ]; then
        [ -n "${LIB_SRC:-}" ] && [ -f "$LIB_SRC/$LIB_NAME" ] \
            || { echo "no Liberty: set LIB to the file or LIB_SRC to its directory" >&2; exit 1; }
        mkdir -p build/lib && cp "$LIB_SRC/$LIB_NAME" $LIB
    fi
fi
# Clock gates, the set/reset scan flop and the hold cell are not for general logic.
DU="-dont_use sg13cmos5l_lgcp_1 -dont_use sg13cmos5l_slgcp_1 -dont_use sg13cmos5l_sdfbbp_1 -dont_use sg13cmos5l_sighold"

top=$1; params=$2; shift 2
rd=""
for f in "$@"; do
    case $f in
        *_bb.v) rd="$rd; read_verilog -lib $f";;
        *)      rd="$rd; read_verilog -sv $f";;
    esac
done
tag=$top$(echo "$params" | tr -d ' -' | tr '=' '_')
out=build/area; mkdir -p $out

yosys -q -l $out/$tag.log -p "read_liberty -lib $LIB $rd; hierarchy -top $top $params; synth -flatten; \
    dfflibmap -liberty $LIB $DU; abc -D 16667 -liberty $LIB $DU; opt_clean; \
    tee -o $out/$tag.stat stat -liberty $LIB" > /dev/null 2>&1 \
    || { echo "yosys failed: $tag"; grep -i error $out/$tag.log | head -5; exit 1; }

area=$(grep -m1 "Chip area" $out/$tag.stat | awk '{print $NF}')
ff=$(grep -E "sg13cmos5l_s?df" $out/$tag.stat | awk '{s += $1} END {print s + 0}')
lat=$(grep -E "sg13cmos5l_dl" $out/$tag.stat | awk '{s += $1} END {print s + 0}')
printf "%-40s area=%12.2f flops=%5d latches=%5d\n" "$tag" "$area" "$ff" "$lat"
