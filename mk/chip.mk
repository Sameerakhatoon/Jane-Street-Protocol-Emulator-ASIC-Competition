# Chip integration: the full-chip top and its source list, the chip cocotb tests.
CHIP_SRC := $(addprefix src/,$(shell sed -n '/^ *source_files:/,/^[^ ]/s/^ *- *"\([^"]*\)".*/\1/p' info_chip.yaml))
CHIP_TOP := $(shell sed -n 's/^ *top_module: *"\([^"]*\)".*/\1/p' info_chip.yaml)

.PHONY: chip-lint chip-test chip-pmem

lint: chip-lint
test: chip-test

# the full-chip top builds from exactly the files info_chip.yaml lists
chip-lint:
	verilator --lint-only -Wall --top-module $(CHIP_TOP) $(CHIP_SRC)
	iverilog -g2012 -o /dev/null -s $(CHIP_TOP) $(CHIP_SRC)
	python3 -c "import json; json.load(open('src/config_chip.json'))"

# program memory alone with stub lanes, then the whole chip, at 3 and 4 lanes
chip-pmem:
	for l in 3 4; do \
	    $(MAKE) -C verif/chip TB=pmem LANES=$$l || exit 1; \
	    ! grep -q '<failure' build/verif-chip/results_pmem_L$$l.xml || exit 1; \
	done

chip-test: chip-pmem
	for l in 3 4; do \
	    $(MAKE) -C verif/chip TB=chip LANES=$$l || exit 1; \
	    ! grep -q '<failure' build/verif-chip/results_chip_L$$l.xml || exit 1; \
	done
