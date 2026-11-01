#!/usr/bin/env bash
# tools/pd/magic-flags.sh: check every Magic DRC flag of a run against the real layout.
# The flow's Magic DRC reads the SRAM as an abstract (MAGIC_DRC_MAGLEFS), so a flag can come
# from the abstract alone. This clips a window around each flag out of the run's real GDS
# (KLayout), runs Magic DRC on that window only, and says whether the same spot is flagged.
# usage: tools/pd/magic-flags.sh <run dir, e.g. runs/rtl6d/proj/runs/wokwi>
# Writes <run dir>/magic-flags/ (clips, per-window reports, summary.txt).
#
# Waiver, rtl6c and rtl6d: Magic reports 51 flags (32 M4.e, 14 M3.f, 5 M1.f), all on the two
# SRAM macros or 0.5 um outside their top and bottom edges. The abstract (src/maglef, from the
# vendor LEF) draws each obstruction layer as one solid rectangle, so the wide-metal .f rules
# fire on our wires beside it, and its pin and obstruction rectangles meet in 0.03 um slivers
# (M4.e); the real layout has neither. This script finds 0 of the 51 on the real GDS (on rtl6b
# it finds 722 of the 727 real pSD.e/f errors, so it does see errors of ours), the IHP KLayout
# deck checks M1.e to M4.f on the real GDS clean, and the Tiny Tapeout precheck runs no Magic
# for IHP. Recheck after any change to the macro placement, the power grid or the abstract.
set -euo pipefail
cd "$(dirname "$0")/../.."
source tools/pd/env.sh
run=$(realpath "$1")
out=$run/magic-flags
rm -rf "$out" && mkdir -p "$out/clips"
image=ghcr.io/librelane/librelane:$(~/ttsetup/venv/bin/python -c 'import librelane; print(librelane.__version__)')
docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:$PWD" -v "$run:$run" -v "$PDK_ROOT:$PDK_ROOT" \
    -e PDK_ROOT="$PDK_ROOT" -e PDK=ihp-sg13cmos5l \
    -e GDS="$(ls "$run"/*-klayout-streamout/*.klayout.gds | head -1)" \
    -e RPT="$(ls "$run"/*-magic-drc/reports/drc.magic.rpt | head -1)" -e OUT="$out" \
    -w "$PWD" "$image" bash -c '
    set -e
    klayout -b -r tools/pd/magic-flags/clip.py
    for g in $OUT/clips/*.gds; do
        GDS=$g MOUT=${g%.gds}.drc magic -dnull -noconsole \
            -rcfile $PDK_ROOT/ihp-sg13cmos5l/libs.tech/magic/ihp-sg13cmos5l.magicrc \
            tools/pd/magic-flags/drc.tcl > ${g%.gds}.log 2>&1
    done
    python3 tools/pd/magic-flags/match.py'
cat "$out/summary.txt"
