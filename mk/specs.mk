# Protocol specs: specs/*.tse, their judge cases in specs/judge and their tests in specs/tests.
SPECS_SRC := $(wildcard specs/*.tse) $(wildcard specs/tests/*.sh) $(wildcard specs/tests/*.txt) \
             $(wildcard specs/judge/*.txt specs/judge/*.expect specs/judge/*.py)

.PHONY: specs-lint specs-test specs-test-slow

# plain ASCII, no tabs, no trailing blanks; the judge self-check compiles
specs-lint:
	@! LC_ALL=C grep -nP '[^\x00-\x7f]' $(SPECS_SRC) || { echo "non-ASCII"; exit 1; }
	@! grep -n "$$(printf '\t')" $(SPECS_SRC) || { echo "tabs"; exit 1; }
	@! grep -n ' $$' $(SPECS_SRC) || { echo "trailing blanks"; exit 1; }
	@python3 -m py_compile specs/judge/selfcheck.py

specs-test:
	bash specs/tests/run-all.sh

# also the build rows whose timing analysis takes many minutes
specs-test-slow:
	SPECS_SLOW=1 bash specs/tests/03-build-outcomes.sh

lint: specs-lint
test: specs-test
