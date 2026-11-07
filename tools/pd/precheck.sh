#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# tools/pd/precheck.sh: Tiny Tapeout precheck on the last run's GDS, without Nix.
# usage: tools/pd/precheck.sh [file.gds]   (reports in tt/precheck/reports/)
# CI runs precheck.py in a Nix shell that only provides KLayout and Magic. The LibreLane
# image already has both plus Python with the klayout module; the missing gdstk, numpy and
# PyYAML come from ~/ttsetup/precheck-py313 (pip --target, Python 3.13 wheels, see below).
#   pip install --target ~/ttsetup/precheck-py313 --python-version 3.13 --only-binary=:all: \
#       --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 gdstk==0.9.60 pyyaml
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${PDK_ROOT:?source ~/tools/chip-env.sh first}"
top=$(sed -n 's/^ *top_module: *"\([^"]*\)".*/\1/p' info.yaml)
gds=$(realpath "${1:-runs/wokwi/final/gds/$top.gds}")
image=ghcr.io/librelane/librelane:$(~/ttsetup/venv/bin/python -c 'import librelane; print(librelane.__version__)')
docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:$PWD" -v "$PDK_ROOT:$PDK_ROOT" \
    -v ~/ttsetup/precheck-py313:/pp -e PYTHONPATH=/pp -e PDK_ROOT="$PDK_ROOT" -e PDK=ihp-sg13cmos5l \
    -w "$PWD/tt/precheck" "$image" bash -c '
    libs=$(dirname $(ls /nix/store/*zlib*/lib/libz.so.1 | head -1)):$(dirname $(ls /nix/store/*gcc*-lib/lib/libstdc++.so.6 | head -1))
    LD_LIBRARY_PATH=$libs python3 precheck.py --gds "$0" --tech ihp-sg13cmos5l' "$gds"
