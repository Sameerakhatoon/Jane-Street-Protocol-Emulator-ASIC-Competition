# SPDX-License-Identifier: Apache-2.0
# Compiler: tsec in tools/tsec, specs in specs/.
PY ?= python3
TSEC_SRC := $(shell find tools/tsec -name '*.py' -o -name '*.sh') $(wildcard specs/*.tse)
TSEC_PROOF := build/tsec-proof

.PHONY: tsec-lint tsec-test tsec-proof tsec-proof-more tsec-fuzz

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
# abc pdr and covered, about 15 minutes. The tasks are named: this SBY reads the "--" line
# before the per-obligation tasks as one more task. The UART receiver (a paced frame, 921,600 baud) is
# generated and elaborated; its bounded run takes hours, so it runs with TSEC_PROOF_UART=1.
# Then one proof per obligation with rIC3 for the 10BASE-T transmitter (a frame role) and the
# I2C target, TSEC_PROOF_JOBS at a time, each within TSEC_PROOF_TIMEOUT seconds: task ob<k>
# must PASS, a timeout or UNKNOWN fails. The obligations not listed (transmitter 0 and 4;
# I2C 0 to 3) did not close in an hour each. Every checker is shown not vacuous in simulation
# with its planted bug LATE = k (no response meets the window): the planted bugs need traces
# too deep for the solver (an address byte, a frame), so tools/tsec/proofs/sim drives legal
# traffic, every checker must be answered, none may fail without a bug and the checker with
# the bug must fail. The transmitter's link pulse (ob0) needs a silent host. Several hours.
TSEC_PROOF_RUNS := spi/proof/tgt $(if $(TSEC_PROOF_UART),uart/proof/rx_side)
TSEC_PROOF_OBS := eth/proof/mac_tx:1,2,3 i2c/proof/tgt:4,5,6,7,8,9
TSEC_PROOF_ETH_SIM := -1:1:400000 1:1:400000 2:1:400000 3:1:400000 4:1:400000 \
	-1:0:1500000 0:0:1500000
TSEC_PROOF_LANE := $(addprefix src/,tse_isa_pkg.sv tse_regs_pkg.sv tse_seq.sv tse_dpll.sv tse_crc.sv \
	tse_resp_rule.sv tse_stuffer.sv tse_codec.sv tse_nco.sv tse_timer.sv tse_lane.sv tse_cfg_bank.sv)
TSEC_PROOF_JOBS ?= 2
TSEC_PROOF_TIMEOUT ?= 3600
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
	$(PY) -m tools.tsec build specs/spi_target.tse -D t_css=142e-9 -D f_sck=1.875e6 --emit proof \
		-o $(TSEC_PROOF)/spi > /dev/null
	cd $(TSEC_PROOF)/uart/proof && yosys -q -p "read_verilog -formal -sv \
		$(addprefix $(CURDIR)/src/,tse_isa_pkg.sv tse_regs_pkg.sv tse_seq.sv tse_dpll.sv tse_crc.sv \
		tse_resp_rule.sv tse_stuffer.sv tse_codec.sv tse_nco.sv tse_timer.sv tse_lane.sv tse_cfg_bank.sv) \
		tse_window_check.sv top_rx_side.sv; prep -top top" > elab.log 2>&1
	@echo "uart/proof/rx_side: elaborated on tse_lane"
	@for t in $(TSEC_PROOF_RUNS); do (cd $(TSEC_PROOF)/$$(dirname $$t) && sby -f $$(basename $$t).sby \
		prove cover > sby.log 2>&1 || true); done
	@for t in $(TSEC_PROOF_RUNS); do for k in prove cover; do \
		got=$$(cat $(TSEC_PROOF)/$${t}_$$k/status 2>/dev/null | cut -d' ' -f1); \
		echo "$$t $$k: $$got (want PASS)"; \
		[ "$$got" = PASS ] || exit 1; \
	done; done
	$(PY) -m tools.tsec build tools/tsec/tests/targets/eth10_tx_window.tse --emit proof \
		-o $(TSEC_PROOF)/eth > /dev/null
	$(PY) -m tools.tsec build specs/i2c_target_fm.tse --emit proof -o $(TSEC_PROOF)/i2c > /dev/null
	@for L in -1 0 1 2 3 4 5 6 7 8 9; do \
		iverilog -g2012 -DNTX=20 -DLATE=$$L -o $(TSEC_PROOF)/i2c/sim.out $(TSEC_PROOF_LANE) \
			$(TSEC_PROOF)/i2c/proof/tse_window_check.sv $(TSEC_PROOF)/i2c/proof/top_tgt.sv \
			tools/tsec/proofs/sim/i2c_ctl_tb.sv 2> /dev/null || exit 1; \
		vvp -n $(TSEC_PROOF)/i2c/sim.out > $(TSEC_PROOF)/i2c/sim$$L.log; \
		n=$$(grep -A1 '^ERROR' $(TSEC_PROOF)/i2c/sim$$L.log | grep -c 'Time: [1-9]'); \
		idle=$$(grep -c 'answered 0$$' $(TSEC_PROOF)/i2c/sim$$L.log); \
		echo "i2c/proof/tgt sim late$$L: $$n failures, $$idle checkers never answered"; \
		bad=$$([ $$L = -1 ] && echo $$n || echo $$((n == 0))); \
		[ $$idle = 0 ] && [ $$bad = 0 ] || exit 1; \
	done
	@for r in $(TSEC_PROOF_ETH_SIM); do \
		L=$${r%%:*}; h=$$(echo $$r | cut -d: -f2); n=$${r##*:}; o=$(TSEC_PROOF)/eth/sim$$L-h$$h; \
		iverilog -g2012 -DHOST=$$h -DNCYC=$$n -DLATE=$$L -o $$o.out $(TSEC_PROOF_LANE) \
			$(addprefix $(TSEC_PROOF)/eth/proof/,tse_window_check.sv top_mac_tx.sv) \
			tools/tsec/proofs/sim/eth_host_tb.sv 2> /dev/null || exit 1; \
		vvp -n $$o.out > $$o.log; \
		f=$$(grep -A1 '^ERROR' $$o.log | grep -c 'Time: [1-9]'); \
		idle=$$(grep -c "ob[$$([ $$h = 1 ] && echo 1-4 || echo 0)] answered 0$$" $$o.log); \
		echo "eth/proof/mac_tx sim late$$L host $$h: $$f failures, $$idle checkers never answered"; \
		bad=$$([ $$L = -1 ] && echo $$f || echo $$((f == 0))); \
		[ $$idle = 0 ] && [ $$bad = 0 ] || exit 1; \
	done
	@for o in $(TSEC_PROOF_OBS); do for k in $$(echo $${o#*:} | tr , ' '); do echo "$${o%:*} ob$$k"; \
		done; done | \
		xargs -P $(TSEC_PROOF_JOBS) -L 1 sh -c 'cd $(TSEC_PROOF)/$$(dirname $$0) && \
		timeout $(TSEC_PROOF_TIMEOUT) sby -f $$(basename $$0).sby $$1 > /dev/null 2>&1; true'
	@for o in $(TSEC_PROOF_OBS); do for k in $$(echo $${o#*:} | tr , ' '); do \
		echo "$${o%:*}_ob$$k PASS"; done; done | \
	while read t want; do \
		got=$$(cat $(TSEC_PROOF)/$$t/status 2>/dev/null | cut -d' ' -f1); \
		echo "$$t: $$got (want $$want)"; \
		[ "$$got" = "$$want" ] || exit 1; \
	done

# More roles than tsec-proof, opt-in: the solver part takes about 3 hours with rIC3, one task
# at a time (each task is a portfolio of some 16 processes: run it alone on the machine).
# Window harnesses: the SPI flash target (a state machine; abc pdr left 9 of 10 open after 45
# minutes, rIC3 proves each alone) and the CAN in-bit ACK receiver (a frame role, rIC3). Their
# planted bugs beyond the first few need traces of 250 cycles or more, too deep for the solver
# in an hour, so a bench drives legal traffic (flash_ctl_tb.sv: SPI mode 0 frames of every
# command; can_tx_tb.sv: stuffed standard frames with a correct CRC, now and then a bad one):
# no bug, no failure and every checker answered; planted bug k, checker k fails. Own-instant
# harnesses (top_<role>_place.sv: paced chains, distances after our own edge, paced-frame
# placements) for the UART transmitter and receiver, the JTAG probe, the SPI controller (mode 0;
# modes 1 to 3 differ only in levels) and the I2C controller (fast mode), each shown live in
# simulation first (tools/tsec/proofs/sim/place_sims.sh: no bug, no failure, every obligation
# checked, every distance inside its window and the analysis' claim; then planted bug k fails,
# for every k). Then every task of TSEC_PROOF2_PASS must PASS and every planted bug of
# TSEC_PROOF2_FAIL must FAIL, TSEC_PROOF_JOBS at a time, each within TSEC_PROOF2_TIMEOUT seconds.
# Left open at 40 minutes each (rIC3): uart_tx and uart_rx obligations 0 and 1, the SPI
# controller's 1 to 10; at an hour each, the CAN receiver's 1, 2, 4, 5 and 6 (its bench
# answers and passes all eight).
TSEC_PROOF2 := build/tsec-proof2
TSEC_PROOF2_BUILDS := uart_tx uart_rx jtag_probe@--pad-skew@0 spi_controller_mode0@--pad-skew@0 \
	i2c_controller_fm spi_flash_target can_rx_ack
TSEC_PROOF2_SIMS := uart_tx:100000:3 uart_rx:200000:40 jtag_probe:100000:3 \
	spi_controller_mode0:100000:3:5:0 i2c_controller_fm:400000:3:8:13
# window benches: role/harness, bench, frames, checkers
TSEC_PROOF2_WSIMS := spi_flash_target/flash:flash_ctl_tb:10:10 can_rx_ack/node:can_tx_tb:12:8
TSEC_PROOF2_PASS := $(foreach k,0 1 2 3 4 5 6 7 8 9,spi_flash_target/flash:ob$(k)) \
	$(foreach k,0 3 7,can_rx_ack/node:ob$(k)) jtag_probe/probe_place:rprove \
	spi_controller_mode0/ctl_place:ric0 $(foreach k,$(shell seq 0 29),i2c_controller_fm/ctl_place:ric$(k))
TSEC_PROOF2_FAIL := $(foreach k,0 1 2,spi_flash_target/flash:late$(k))
TSEC_PROOF2_TIMEOUT ?= 3600
tsec-proof-more:
	rm -rf $(TSEC_PROOF2)
	mkdir -p $(TSEC_PROOF2)
	@for b in $(TSEC_PROOF2_BUILDS); do n=$${b%%@*}; a=$$(echo $$b | cut -s -d@ -f2- | tr @ ' '); \
		$(PY) -m tools.tsec build specs/$$n.tse $$a --emit proof -o $(TSEC_PROOF2)/$$n > /dev/null || exit 1; \
	done
	@for s in $(TSEC_PROOF2_SIMS); do \
		bash tools/tsec/proofs/sim/place_sims.sh $(TSEC_PROOF2)/$${s%%:*}/proof $$(echo $${s#*:} | tr : ' ') \
			|| exit 1; \
	done
	@for w in $(TSEC_PROOF2_WSIMS); do d=$${w%%:*}; tb=$$(echo $$w | cut -d: -f2); \
		ntx=$$(echo $$w | cut -d: -f3); ng=$${w##*:}; o=$(TSEC_PROOF2)/$$(dirname $$d); \
		for L in -1 $$(seq 0 $$((ng - 1))); do \
			iverilog -g2012 -DNTX=$$ntx -DLATE=$$L -o $$o/sim.out $(TSEC_PROOF_LANE) \
				$$(ls $$o/proof/tse_*_check.sv) $$o/proof/top_$$(basename $$d).sv \
				tools/tsec/proofs/sim/$$tb.sv 2> /dev/null || exit 1; \
			vvp -n $$o/sim.out > $$o/sim$$L.log; \
			n=$$(grep -A1 '^ERROR' $$o/sim$$L.log | grep -c 'Time: [1-9]'); \
			idle=$$(grep -c 'answered 0$$' $$o/sim$$L.log); \
			echo "$$d sim late$$L: $$n failures, $$idle checkers never answered"; \
			bad=$$([ $$L = -1 ] && echo $$n || echo $$((n == 0))); \
			[ $$idle = 0 ] && [ $$bad = 0 ] || exit 1; \
		done; \
	done
	@for t in $(TSEC_PROOF2_PASS) $(TSEC_PROOF2_FAIL); do echo "$$t"; done | \
		xargs -P $(TSEC_PROOF_JOBS) -L 1 sh -c 'd=$${0%%:*}; cd $(TSEC_PROOF2)/$$(dirname $$d)/proof && \
		timeout $(TSEC_PROOF2_TIMEOUT) sby -f $$(basename $$d).sby $${0#*:} > /dev/null 2>&1; true'
	@for t in $(TSEC_PROOF2_PASS); do echo "$$t PASS"; done > $(TSEC_PROOF2)/want
	@for t in $(TSEC_PROOF2_FAIL); do echo "$$t FAIL"; done >> $(TSEC_PROOF2)/want
	@while read t want; do d=$${t%%:*}; \
		got=$$(cat $(TSEC_PROOF2)/$$(dirname $$d)/proof/$$(basename $$d)_$${t#*:}/status 2>/dev/null | cut -d' ' -f1); \
		echo "$$t: $$got (want $$want)"; \
		[ "$$got" = "$$want" ] || exit 1; \
	done < $(TSEC_PROOF2)/want

lint: tsec-lint
test: tsec-test
