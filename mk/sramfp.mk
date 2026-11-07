# SPDX-License-Identifier: Apache-2.0
# SRAM output paths: proof that the program SRAM's read data never steers the
# SRAM's own inputs in the same cycle (the layout constraints exempt those
# paths from timing on that ground), with planted bugs that must fail.
RTL_DIR      ?= src
SRAMFP_BUILD ?= build/sramfp
SRAMFP_JOBS  ?= 2

.PHONY: sramfp sramfp-proof sramfp-lint

lint: sramfp-lint
formal: sramfp

sramfp-lint:
	python3 -m py_compile formal/sramfp/run.py formal/sramfp/product.py

# proof and planted bugs
sramfp:
	python3 formal/sramfp/run.py --rtl $(RTL_DIR) --build $(SRAMFP_BUILD) --jobs $(SRAMFP_JOBS)

# proof only
sramfp-proof:
	python3 formal/sramfp/run.py --rtl $(RTL_DIR) --build $(SRAMFP_BUILD) --jobs $(SRAMFP_JOBS) --no-bugs
