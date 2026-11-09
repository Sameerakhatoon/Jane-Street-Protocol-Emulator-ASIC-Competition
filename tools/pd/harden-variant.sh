#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# tools/pd/harden-variant.sh: harden another top (for example the full chip) without touching
# the committed default. Builds a throwaway Tiny Tapeout project in runs/<tag>/proj from the
# given info.yaml and config, with a copy of src/, and runs LibreLane there.
# usage: tools/pd/harden-variant.sh <info.yaml> <config.json> <tag> [LibreLane step to stop after]
# PD_SRC=<dir> takes the sources from <dir> instead of src/.
#   e.g. tools/pd/harden-variant.sh info.yaml src/config.json chip1 OpenROAD.GlobalRouting
# The run lands in runs/<tag>/proj/runs/wokwi, so tools/pd/precheck.sh <that gds> finds the right info.yaml.
set -euo pipefail
cd "$(dirname "$0")/../.."
source tools/pd/env.sh
info=$1 cfg=$2 tag=$3 to=${4:-}
p=runs/$tag/proj
rm -rf "runs/$tag"
mkdir -p $p/runs/wokwi
cp -r "${PD_SRC:-src}" $p/src
cp "$info" $p/info.yaml
cp "$cfg" $p/src/config.json
ln -s "$PWD/tt" $p/tt
start=$(date +%s)
(cd $p && ./tt/tt_tool.py --create-user-config --ihp)
python -m librelane -j "$PD_JOBS" --pdk-root "$PDK_ROOT" --docker-no-tty --dockerized \
    --pdk ihp-sg13cmos5l --manual-pdk --run-tag wokwi --force-run-dir $p/runs/wokwi \
    ${to:+--to "$to"} $p/src/config_merged.json 2>&1 | tee "runs/$tag/harden.log"
[ -z "$to" ] && python tools/pd/corners.py $p/runs/wokwi/final/metrics.json --require typ | tee -a "runs/$tag/harden.log" || true
echo "wall time: $(( ($(date +%s) - start) / 60 )) min" | tee -a "runs/$tag/harden.log"
