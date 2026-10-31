# End-to-end examples: a spec compiled, loaded into the Verilated full chip
# through the host library, run, and judged from the pins-only trace.
.PHONY: e2e-lint e2e-uart e2e-i2c e2e-combo e2e-flash e2e-test
e2e-lint:
	python3 -m py_compile examples/*.py examples/*/*.py
	bash -n examples/*/run.sh examples/tests/*.sh
e2e-uart:
	bash examples/uart/run.sh
e2e-combo:
	bash examples/combo/run.sh
e2e-i2c:
	bash examples/i2c/run.sh
e2e-flash:
	bash examples/flash/run.sh
	bash examples/flash/run.sh --lanes
e2e-test:
	bash examples/tests/run-all.sh
lint: e2e-lint
test: e2e-test
