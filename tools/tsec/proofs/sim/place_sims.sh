#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# place_sims.sh PROOFDIR NCYC PINHOLD [HOSTMAX HOSTSHIFT]: show the own-instant harness in
# PROOFDIR live with place_tb.sv. LATE = -1: no failure, every obligation checked, and every
# logged distance inside its window and the analysis' claim (place_claims.py). LATE = k for each
# obligation k: at least one failure. One line per run; exit 1 on the first wrong outcome.
set -u
d=$1; ncyc=$2; hold=$3; hmax=${4:-}; hshift=${5:-0}
here=$(dirname "$0")
src=$(cd "$here/../../../../src" && pwd)
rtl=""
for f in tse_isa_pkg tse_regs_pkg tse_seq tse_dpll tse_crc tse_resp_rule tse_stuffer tse_codec tse_nco \
         tse_timer tse_lane tse_cfg_bank; do rtl="$rtl $src/$f.sv"; done
top=$(ls "$d"/top_*_place.sv)
table=$(ls "$d"/*_place.txt)
ng=$(grep -c '^ob' "$table")
defs="-DNG=$ng -DNCYC=$ncyc -DPINHOLD=$hold"
[ -n "$hmax" ] && defs="$defs -DHOSTMAX=$hmax -DHOSTSHIFT=$hshift"
grep -q 'tse_cfg_bank #' "$top" && defs="$defs -DBANK"
for L in $(seq -1 $((ng - 1))); do
    o="$d/sim$L"
    log=$([ "$L" = -1 ] && echo -DPLACE_LOG)
    iverilog -g2012 $defs -DLATE=$L $log -o "$o.out" $rtl "$top" "$here/place_tb.sv" 2> /dev/null || exit 1
    vvp -n "$o.out" > "$o.log"
    f=$(grep -A1 '^ERROR' "$o.log" | grep -c 'Time: [1-9]')
    idle=$(grep -c 'answered 0$' "$o.log")
    echo "$(basename "$top" .sv) sim late$L: $f failures, $idle obligations never checked"
    if [ "$L" = -1 ]; then
        [ "$f" = 0 ] && [ "$idle" = 0 ] && python3 "$here/place_claims.py" "$table" "$o.log" || exit 1
    else
        [ "$f" != 0 ] || exit 1
    fi
done
