# CI helpers: the structural check of .github/ and the regression steps as the
# JSON job matrix of the verify workflow, so the step list lives in regress.mk.
# The Check and Trigger demo joins as its own job until regress.mk has it.
PY ?= python3
CI_STEPS = $(REGRESS_STEPS) $(if $(filter check=%,$(REGRESS_STEPS)),,check=check-demo)

.PHONY: ci-check ci-matrix
lint: ci-check

ci-check:
	$(PY) .github/check_workflows.py

ci-matrix:
	@$(PY) -c 'import json, sys; print(json.dumps({"include": [dict(zip(("step", "targets"), \
	    s.split("=", 1))) for s in sys.argv[1:]]}))' $(CI_STEPS)
