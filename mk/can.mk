# CAN receive with the ACK in the slot on the simulated chip, judged on the bus
# (the e2e step runs it too, as examples/tests/09-can.sh).
.PHONY: e2e-can
e2e-can:
	bash examples/can/run.sh
