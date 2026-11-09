# SPDX-License-Identifier: Apache-2.0
# Chip integration: the full-chip top and its source list, the chip cocotb tests.
CHIP_SRC := $(addprefix src/,$(shell sed -n '/^ *source_files:/,/^[^ ]/s/^ *- *"\([^"]*\)".*/\1/p' info.yaml))
CHIP_TOP := $(shell sed -n 's/^ *top_module: *"\([^"]*\)".*/\1/p' info.yaml)

.PHONY: chip-lint chip-test chip-pmem

lint: chip-lint
test: chip-test

# the full-chip top builds from exactly the files info.yaml lists, plus the
# stand-in for its one PDK cell
chip-lint:
	verilator --lint-only -Wall --top-module $(CHIP_TOP) $(CHIP_SRC) test/rtl/sg13cmos5l_antennanp.v
	iverilog -g2012 -o /dev/null -s $(CHIP_TOP) $(CHIP_SRC) test/rtl/sg13cmos5l_antennanp.v
	python3 -c "import json; json.load(open('src/config.json'))"

# program memory alone with stub lanes, then the whole chip
chip-pmem:
	$(MAKE) -C verif/chip TB=pmem
	! grep -q '<failure' build/verif-chip/results_pmem_L2.xml

chip-test: chip-pmem
	$(MAKE) -C verif/chip TB=chip
	! grep -q '<failure' build/verif-chip/results_chip_L2.xml
