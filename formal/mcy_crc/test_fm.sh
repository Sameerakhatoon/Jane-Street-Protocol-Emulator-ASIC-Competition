#!/bin/bash
# kill test: the catalogue check values on the mutated CRC unit
exec 2>&1
set -ex
bash $SCRIPTS/create_mutated.sh -o mutated.il
ln -sf ../../crc_props.sv ../../test_fm.sby .
sby -f test_fm.sby
r=PASS
for t in test_fm_crc32 test_fm_crc15; do grep -q PASS $t/status || r=FAIL; done
echo "1 $r" >> output.txt
exit 0
