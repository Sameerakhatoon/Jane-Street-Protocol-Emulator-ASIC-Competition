# Chip integration: the full-chip top and its source list, the chip cocotb tests.
CHIP_SRC := $(addprefix src/,$(shell sed -n '/^ *source_files:/,/^[^ ]/s/^ *- *"\([^"]*\)".*/\1/p' info_chip.yaml))
CHIP_TOP := $(shell sed -n 's/^ *top_module: *"\([^"]*\)".*/\1/p' info_chip.yaml)

.PHONY: chip-lint chip-test

lint: chip-lint
test: chip-test

# the full-chip top builds from exactly the files info_chip.yaml lists
chip-lint:
	verilator --lint-only -Wall --top-module $(CHIP_TOP) $(CHIP_SRC)
	iverilog -g2012 -o /dev/null -s $(CHIP_TOP) $(CHIP_SRC)
	python3 -c "import json; json.load(open('src/config_chip.json'))"

chip-test:
	$(MAKE) -C verif/chip LANES=3
	! grep -q '<failure' build/verif-chip/results_L3.xml
	$(MAKE) -C verif/chip LANES=4
	! grep -q '<failure' build/verif-chip/results_L4.xml
