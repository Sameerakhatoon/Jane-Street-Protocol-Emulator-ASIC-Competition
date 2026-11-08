#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# place_lemmas.sh PROOFDIR NCYC PINHOLD [HOSTMAX HOSTSHIFT [RXRE]] (HOSTMAX - for none): chain lemmas
# for the own-instant harness in PROOFDIR (place_lemmas.py), mined from a place_tb.sv run (PROBE, LEMLOG). With RXRE, a
# second run with a slow host (RXRE 2000) leaves free every lemma whose range it changes. The lemmas
# are then checked live in a run with other pin timing (and, with RXRE, a slow host of RXRE): no
# failure. A wrong lemma that survives fails the proof (the tasks assert it), never passes it.
set -u
d=$1; ncyc=$2; hold=$3; hmax=${4:-}; hshift=${5:-0}; rxre=${6:-}
[ "$hmax" = - ] && hmax=""
here=$(dirname "$0")
src=$(cd "$here/../../../../src" && pwd)
rtl=""
for f in tse_isa_pkg tse_regs_pkg tse_seq tse_dpll tse_crc tse_resp_rule tse_stuffer tse_codec tse_nco \
         tse_timer tse_lane tse_cfg_bank; do rtl="$rtl $src/$f.sv"; done
top=$(ls "$d"/top_*_place.sv)
ng=$(grep -c '^ob' "$d"/*_place.txt)
defs="-DNG=$ng -DLATE=-1 -DPROBE"
[ -n "$hmax" ] && defs="$defs -DHOSTMAX=$hmax -DHOSTSHIFT=$hshift"
sim() {  # name ncyc hold extra-defines
    iverilog -g2012 $defs -DNCYC=$2 -DPINHOLD=$3 $4 -o "$d/$1.out" $rtl "$top" "$here/place_tb.sv" \
        2> /dev/null || exit 1
    vvp -n "$d/$1.out" > "$d/$1.log"
    f=$(grep -A1 '^ERROR' "$d/$1.log" | grep -c 'Time: [1-9]')
    echo "$(basename "$top" .sv) $1: $f failures"
    [ "$f" = 0 ] || exit 1
}
sim lemlog "$ncyc" "$hold" -DLEMLOG
slow=""
if [ -n "$rxre" ]; then
    sim lemslow $((ncyc * 3)) "$hold" "-DLEMLOG -DRXRE=2000"
    slow="$d/lemslow.log"
fi
python3 "$here/place_lemmas.py" "$top" "$d/lemlog.log" 0 $slow || exit 1
sim lemcheck $((ncyc * 2)) $((hold + 2)) ""
[ -z "$rxre" ] || sim lemcheckslow $((ncyc * 2)) $((hold + 2)) "-DRXRE=$rxre"
