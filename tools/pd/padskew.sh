#!/usr/bin/env bash
# tools/pd/padskew.sh: tile-internal pin delays and pad skew of a finished full-chip run, for
# tsec build --pad-min, --pad-max and --pad-skew. OpenSTA on the run's final netlist, SDC and
# SPEF at each signoff corner; definitions in tools/pd/padskew/padskew.py.
# usage: tools/pd/padskew.sh [run dir, default runs/rtl6c/proj/runs/wokwi]
# Writes build/padskew/<run>/<corner>.txt and prints the summary.
set -euo pipefail
cd "$(dirname "$0")/../.."
source tools/pd/env.sh
run=${1:-runs/rtl6c/proj/runs/wokwi}
final=$PWD/$run/final
out=build/padskew/$(basename "${run%/proj/runs/wokwi}")
mkdir -p "$out"
lib=$PDK_ROOT/ihp-sg13cmos5l/libs.ref
image=ghcr.io/librelane/librelane:$(~/ttsetup/venv/bin/python -c 'import librelane; print(librelane.__version__)')
# corner, standard cell library, SRAM library (the SRAM has no -40 C fast library; the flow uses -55 C)
for c in "nom_fast_1p32V_m40C fast_1p32V_m40C fast_1p32V_m55C" \
         "nom_typ_1p20V_25C typ_1p20V_25C typ_1p20V_25C" \
         "nom_slow_1p08V_125C slow_1p08V_125C slow_1p08V_125C"; do
    set -- $c
    docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:$PWD" -v "$PDK_ROOT:$PDK_ROOT" -w "$PWD" \
        -e LIBS="$lib/sg13cmos5l_stdcell/lib/sg13cmos5l_stdcell_$2.lib $lib/sg13cmos5l_sram/lib/RM_IHPSG13_1P_512x16_c2_bm_bist_$3.lib" \
        -e NETLIST="$final/nl/tt_um_tse_sk_chip.nl.v" -e SDC="$final/sdc/tt_um_tse_sk_chip.sdc" \
        -e SPEF="$final/spef/nom/tt_um_tse_sk_chip.nom.spef" \
        "$image" sta -no_splash -exit tools/pd/padskew/paths.tcl > "$out/$1.txt" 2>&1
done
python3 tools/pd/padskew/padskew.py "$out"/nom_fast_1p32V_m40C.txt "$out"/nom_typ_1p20V_25C.txt "$out"/nom_slow_1p08V_125C.txt
