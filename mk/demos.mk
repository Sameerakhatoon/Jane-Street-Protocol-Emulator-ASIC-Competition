# SPDX-License-Identifier: Apache-2.0
# Real host software against the Verilated full chip (tse_chip, two lanes), each
# lane running a program compiled from specs/ by tools.tsec, judged on the pins.
#   demo-jtag      OpenOCD reads the IDCODE through the JTAG probe lane
#   demo-swd       OpenOCD reads DPIDR and powers up an SW-DP through the SWD probe lane
#   demo-flash     a serprog client reads the SPI flash lane; the controller is a lane too
#   demo-can       CAN node of the simulator, fed can_frame records, against the CAN lane
#   demo-ping      the Linux kernel answers frames from the 10BASE-T transmit lane
# The stand-in demos (judges-demos) are separate and unchanged.
.PHONY: demos-lint demo-jtag demo-swd demo-flash demo-can demo-ping demos
demos-lint:                     # e2e-lint covers the Python and every run.sh
	bash -n examples/can/run_node.sh examples/eth10/run_ping.sh
demo-jtag:
	bash examples/jtag/run.sh
demo-swd:
	bash examples/swd/run.sh
demo-flash:
	bash examples/flash/run.sh --lanes
demo-can:
	bash examples/can/run_node.sh
demo-ping:
	bash examples/eth10/run_ping.sh
demos: demo-jtag demo-swd demo-flash demo-can demo-ping
lint: demos-lint
