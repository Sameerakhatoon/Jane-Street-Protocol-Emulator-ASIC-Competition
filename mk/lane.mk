# SPDX-License-Identifier: Apache-2.0
# Lane alignment: area of the lane blocks and the full chip (needs LIB or LIB_SRC
# like make area).
.PHONY: lane-area

lane-area:
	bash tools/pd/area-lane.sh
