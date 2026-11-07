#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# tools/pd/harden.sh: one local hardening run, the same LibreLane command the Tiny Tapeout GDS action runs.
# usage: tools/pd/harden.sh [tag]   (log in harden-<tag>.log, finished run copied to runs/keep/<tag>)
# LibreLane is called directly: tt_tool.py --harden builds the same command line but stops on a
# repository without a git remote (it writes the remote into commit_id.json).
set -euo pipefail
cd "$(dirname "$0")/../.."
source tools/pd/env.sh
tag=${1:-$(date +%Y%m%d-%H%M)}
log=harden-$tag.log
start=$(date +%s)
./tt/tt_tool.py --create-user-config --ihp
rm -rf runs/wokwi && mkdir -p runs/wokwi
python -m librelane -j "$PD_JOBS" --pdk-root "$PDK_ROOT" --docker-no-tty --dockerized \
    --pdk ihp-sg13cmos5l --manual-pdk --run-tag wokwi --force-run-dir runs/wokwi \
    src/config_merged.json 2>&1 | tee "$log"
for what in warnings stats cell-category; do
    ./tt/tt_tool.py --print-$what --ihp | tee -a "$log"
done
python tools/pd/corners.py runs/wokwi/final/metrics.json --require typ | tee -a "$log" || true
mkdir -p runs/keep
rm -rf "runs/keep/$tag"
cp -r runs/wokwi "runs/keep/$tag"
echo "wall time: $(( ($(date +%s) - start) / 60 )) min" | tee -a "$log"
