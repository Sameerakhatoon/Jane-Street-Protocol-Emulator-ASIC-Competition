# SPDX-License-Identifier: Apache-2.0
# tools/pd/env.sh: sourced by the pd scripts. Puts the tt-support-tools venv first on PATH
# without "activate", whose deactivate() clashes with the OSS CAD Suite's, and drops the
# cocotb PYTHONPATH so the venv's own packages win.
: "${PDK_ROOT:?source ~/tools/chip-env.sh first (PDK_ROOT is the folder that contains ihp-sg13cmos5l)}"
export PDK=ihp-sg13cmos5l
export PATH=~/ttsetup/venv/bin:$PATH
unset PYTHONPATH PYTHONHOME
# At most 6 LibreLane and OpenROAD threads by default, so a run leaves memory for other work.
export PD_JOBS=${PD_JOBS:-6}
