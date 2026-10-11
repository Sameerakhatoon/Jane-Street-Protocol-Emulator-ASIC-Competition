# Simulation harness: sim/ has its own plain Makefile and tests.
.PHONY: sim sim-lint sim-test sim-clean
sim:
	$(MAKE) -C sim
sim-lint:
	$(MAKE) -C sim lint
sim-test:
	$(MAKE) -C sim test
sim-clean:
	$(MAKE) -C sim clean
lint: sim-lint
test: sim-test
