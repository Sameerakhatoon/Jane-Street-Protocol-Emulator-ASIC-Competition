#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# tools/pd/magic-flags.sh: check every Magic DRC flag of a run against the real layout.
# The flow's Magic DRC reads the SRAM as an abstract (MAGIC_DRC_MAGLEFS), so a flag can come
# from the abstract alone. This clips a window around each flag out of the run's real GDS
# (KLayout), runs Magic DRC on that window only, and says whether the same spot is flagged.
# usage: tools/pd/magic-flags.sh <run dir, e.g. runs/rtl6d/proj/runs/wokwi>
# Writes <run dir>/magic-flags/ (clips, per-window reports, summary.txt).
#
# The SRAM abstract (src/maglef) carries the macro's pins only. Its obstruction layers drew
# each metal as one solid rectangle, so the wide-metal .f rules fired on our wires beside
# it and its pin and obstruction rectangles met in 0.03 um slivers (M4.e), 53 flags on the
# 8x4 chip that the real layout does not have. The router still keeps out of the macro
# through its LEF obstructions, and the IHP KLayout deck checks our wiring against the
# real SRAM GDS. The vendor SRAM GDS itself is not Magic-clean (57 Cnt.c flags even with
# the PDK's read_sram_gds.tcl flattening), so the flow keeps the abstract.
# Magic also flags the standard-cell fill cells (pSD.e/f) wherever two of them sit directly
# above each other across a shared rail; the PDK's decap cells, which fill large gaps
# first, do not have the diffusion stub that causes it.
# Recheck after any change to the macro placement, the power grid or the abstract.
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
