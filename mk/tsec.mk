# Compiler: tsec in tools/tsec, specs in specs/.
PY ?= python3
TSEC_SRC := $(shell find tools/tsec -name '*.py' -o -name '*.sh') $(wildcard specs/*.tse)
TSEC_PROOF := build/tsec-proof

.PHONY: tsec-lint tsec-test tsec-proof tsec-fuzz

# byte-compile, then style: no tabs, no trailing blanks, lines up to 110, no en or em dashes
tsec-lint:
	$(PY) -m compileall -q tools/tsec
	@! grep -n "$$(printf '\t')" $(filter %.py %.tse,$(TSEC_SRC)) || { echo "tabs"; exit 1; }
	@! grep -n ' $$' $(TSEC_SRC) || { echo "trailing blanks"; exit 1; }
	@! awk 'length > 110 { print FILENAME ":" FNR ": line too long"; bad = 1 } END { exit !bad }' \
		$(filter %.py,$(TSEC_SRC))
	@! LC_ALL=C.UTF-8 grep -nP '[\x{2013}\x{2014}]' $(TSEC_SRC) || { echo "dashes"; exit 1; }
	verilator --lint-only -Wall -GLO=3 -GHI=4 tools/tsec/proofs/templates/tse_window_check.sv

tsec-test:
	bash tools/tsec/tests/run-all.sh

# 2500 Hypothesis examples for each of the four front-end fuzz tests: 10,000 in all.
tsec-fuzz:
	TSEC_FUZZ_EXAMPLES=2500 $(PY) -m pytest -q -p no:cacheprovider tools/tsec/tests/unit/test_10_fuzz.py

# The window checker's own regression (ok PASS, tight FAIL, bug FAIL, cov PASS), then the
# generated harnesses on the real lane RTL. The SPI target (a state machine) is proved with
# abc pdr and covered, about 15 minutes. The UART receiver (a paced frame, 921,600 baud) is
# generated and elaborated; its bounded run takes hours, so it runs with TSEC_PROOF_UART=1.
TSEC_PROOF_RUNS := spi/proof/tgt $(if $(TSEC_PROOF_UART),uart/proof/rx_side)
tsec-proof:
	rm -rf $(TSEC_PROOF)
	mkdir -p $(TSEC_PROOF)
	cp tools/tsec/proofs/demo/* $(TSEC_PROOF)/
	cp tools/tsec/proofs/templates/tse_window_check.sv $(TSEC_PROOF)/
	cd $(TSEC_PROOF) && sby -f window.sby > sby.log 2>&1 || true
	@for t in ok:PASS tight:FAIL bug:FAIL cov:PASS; do \
		got=$$(cat $(TSEC_PROOF)/window_$${t%%:*}/status 2>/dev/null | cut -d' ' -f1); \
		echo "window $${t%%:*}: $$got (want $${t#*:})"; \
		[ "$$got" = "$${t#*:}" ] || exit 1; \
	done
	$(PY) -m tools.tsec build specs/uart.tse -D baud=921600 --emit proof -o $(TSEC_PROOF)/uart > /dev/null
	$(PY) -m tools.tsec build specs/spi_target.tse -D t_css=117e-9 -D f_sck=1.875e6 --emit proof \
		-o $(TSEC_PROOF)/spi > /dev/null
	cd $(TSEC_PROOF)/uart/proof && yosys -q -p "read_verilog -formal -sv \
		$(addprefix $(CURDIR)/src/,tse_isa_pkg.sv tse_regs_pkg.sv tse_seq.sv tse_dpll.sv tse_crc.sv \
		tse_resp_rule.sv tse_stuffer.sv tse_codec.sv tse_nco.sv tse_timer.sv tse_lane.sv) \
		tse_window_check.sv top_rx_side.sv; prep -top top" > elab.log 2>&1
	@echo "uart/proof/rx_side: elaborated on tse_lane"
	@for t in $(TSEC_PROOF_RUNS); do (cd $(TSEC_PROOF)/$$(dirname $$t) && sby -f $$(basename $$t).sby \
		> sby.log 2>&1 || true); done
	@for t in $(TSEC_PROOF_RUNS); do for k in prove cover; do \
		got=$$(cat $(TSEC_PROOF)/$${t}_$$k/status 2>/dev/null | cut -d' ' -f1); \
		echo "$$t $$k: $$got (want PASS)"; \
		[ "$$got" = PASS ] || exit 1; \
	done; done

lint: tsec-lint
test: tsec-test
