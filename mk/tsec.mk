# Compiler: tsec in tools/tsec, specs in specs/.
PY ?= python3
TSEC_SRC := $(shell find tools/tsec -name '*.py' -o -name '*.sh') $(wildcard specs/*.tse)
TSEC_PROOF := build/tsec-proof

.PHONY: tsec-lint tsec-test tsec-proof

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

# The window checker's own regression: ok PASS, tight FAIL, bug FAIL, cov PASS.
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

lint: tsec-lint
test: tsec-test
