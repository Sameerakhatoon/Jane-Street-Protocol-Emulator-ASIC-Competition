# SPDX-License-Identifier: Apache-2.0
# Whole-repository regression: every area's targets, one step after the
# other with one make job, a log per step in build/regress/ and a summary
# table at the end. A step is name=target[,target...];
# make regress REGRESS_STEPS="rtl=rtl-lint,rtl-sim chip=chip-test" runs a subset.
REGRESS_DIR ?= build/regress
REGRESS_STEPS ?= lint=lint isa=isa-test,isa-check,isa-seqspec tsec=tsec-test specs=specs-test \
                 rtl=rtl-lint,rtl-sim,rtl-test chip=chip-lint,chip-test host=host-check,host-test,host-rtl \
                 verif=verif-test formal=formal judges=judges-test tools=tools-test sim=sim-test
REGRESS_STEPS += e2e=e2e-test check=check-demo

.PHONY: regress
regress:
	@mkdir -p $(REGRESS_DIR); : > $(REGRESS_DIR)/summary.txt; bad=0; \
	for step in $(REGRESS_STEPS); do \
	    name=$${step%%=*}; targets=$$(tr ',' ' ' <<< "$${step#*=}"); \
	    printf '%-8s %s ... ' "$$name" "$$targets"; t0=$$(date +%s); \
	    if $(MAKE) --no-print-directory -j1 $$targets > $(REGRESS_DIR)/$$name.log 2>&1; then r=PASS; \
	    else r=FAIL; bad=1; fi; \
	    dt=$$(( $$(date +%s) - t0 )); echo "$$r ($${dt}s)"; \
	    printf '%-8s %-4s %6ss  %s\n' "$$name" "$$r" "$$dt" "$$targets" >> $(REGRESS_DIR)/summary.txt; \
	done; \
	echo; printf '%-8s %-4s %7s  %s\n' step res time targets; cat $(REGRESS_DIR)/summary.txt; \
	echo "logs in $(REGRESS_DIR)/"; exit $$bad
