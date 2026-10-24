# Independent protocol judges: verif/judges (Python standard library only).
# JUDGES_HYP_EXAMPLES sets the Hypothesis examples per property test.
PY ?= python3
JUDGES_HYP_EXAMPLES ?= 25
JUDGES_PY := $(wildcard verif/judges/*.py verif/judges/tests/*.py)

.PHONY: judges-lint judges-test
lint: judges-lint
test: judges-test

judges-lint:
	$(PY) -m py_compile $(JUDGES_PY)
	bash -n verif/judges/demos/record.sh
	@if command -v iverilog >/dev/null; then \
	    mkdir -p build/judges && iverilog -g2012 -o build/judges/lint.vvp \
	        verif/judges/tests/rtl/uart_tx_tb.sv src/tse_nco.sv src/tse_uart_tx.sv; fi

judges-test:
	JUDGES_HYP_EXAMPLES=$(JUDGES_HYP_EXAMPLES) HYPOTHESIS_STORAGE_DIRECTORY=build/hypothesis \
	    $(PY) -m pytest -q -p no:cacheprovider -rs verif/judges/tests

# make judges-run [JUDGES_MANIFEST=path]: judge every case of a manifest; a skip
# fails. The default manifest holds the stand-in demos (OpenOCD JTAG and SWD,
# TAP ping); make judges-demos records their traces first.
JUDGES_MANIFEST ?= verif/judges/demos/stand_in.txt
.PHONY: judges-run judges-demos
judges-run:
	$(PY) -m verif.judges.run_judges $(JUDGES_MANIFEST)
judges-demos:
	bash verif/judges/demos/record.sh
