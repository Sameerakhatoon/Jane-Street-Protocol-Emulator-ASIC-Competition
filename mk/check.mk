# SPDX-License-Identifier: Apache-2.0
# Check and Trigger mode on the Verilated chip: hand-written monitor lanes
# watch an I2C bus, host/tse_check reports the timing rules, the judges
# confirm from the pins-only trace.
.PHONY: check-lint check-clean check-thd-sta check-sweep check-trigger check-demo
check-lint:
	python3 -m py_compile examples/check/*.py examples/trigger/*.py
	bash -n examples/check/run.sh examples/trigger/run.sh examples/check/tests/*.sh
	for p in examples/check/i2c_edges.s examples/check/i2c_cond.s examples/trigger/i2c_bytes.s; do \
	    python3 -m tools.tseasm $$p -o /dev/null || exit 1; done
check-clean:
	bash examples/check/run.sh clean
check-thd-sta:
	bash examples/check/run.sh thd-sta
check-sweep:
	bash examples/check/run.sh sweep
check-trigger:
	bash examples/trigger/run.sh
check-demo:
	bash examples/check/tests/run-all.sh
lint: check-lint
test: check-demo
