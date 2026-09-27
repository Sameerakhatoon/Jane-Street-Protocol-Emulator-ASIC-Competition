# Hygiene and Check-mode tools: tools/hygiene, host/tse_check.
# make hygiene-log RANGE=cmos5l..HEAD checks commit messages.
PY ?= python3
RANGE ?= HEAD
TOOLS_TESTS := host/tse_check/tests tools/hygiene/tests

.PHONY: hygiene hygiene-log tools-test

hygiene:
	$(PY) tools/hygiene/check.py

hygiene-log:
	$(PY) tools/hygiene/check.py --commits $(RANGE)

tools-test:
	HYPOTHESIS_STORAGE_DIRECTORY=build/hypothesis $(PY) -m pytest -q -p no:cacheprovider $(TOOLS_TESTS)

lint: hygiene
test: tools-test
