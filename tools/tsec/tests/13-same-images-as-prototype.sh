#!/usr/bin/env bash
# The images are word for word the ones the first prototype compiler produced for the same runs,
# after the transmitter's two-word output prologue (idle level, pad on) the prototype lacked:
# drop it and move the jump targets (opcode 0, target in the low byte) back by two.
. "$(dirname "$0")/lib.sh"
unprologue() {
    local first="$(echo "$1" | cut -d' ' -f1-2)"
    assert_eq "4011 4111" "$first" "tx output prologue"
    for w in $(echo "$1" | cut -d' ' -f3-); do
        case "$w" in
            0*) printf "%04x " $(( 0x$w - 2 )) ;;
            *) printf "%s " "$w" ;;
        esac
    done | sed 's/ $//'
}
check() {
    local want_tx="$1" want_rx="$2"
    shift 2
    out="$(tsec build specs/uart.tse "$@")"
    imgs="$(echo "$out" | sed -n 's/^ACCEPTED: [0-9]* instructions, image //p')"
    assert_eq "$want_tx" "$(unprologue "$(echo "$imgs" | sed -n 1p)")" "tx image $*"
    assert_eq "$want_rx" "$(echo "$imgs" | sed -n 2p)" "rx image $*"
}
check "c300 4207 4010 e1ff e006 e003 8000 e1ff e005 0206 4011 e1ff e005 0000" \
      "2000 e0fe e000 4207 0500 e1ff e006 e003 a000 e1ff e005 0208 050e 4601 c100 0000"
check "c300 4207 4010 e040 8000 e03e 0204 4011 e03e 0000" \
      "2000 e01b e000 4207 0500 e040 a000 e03e 0206 050b 4601 c100 0000" -D baud=921600
check "c300 e000 4710 4010 4207 e000 25a0 8000 0206 e000 25a0 4011 25a0 4720 0000" \
      "2000 e01d 4710 0500 4207 e000 25a0 a000 0206 e000 25a0 050d 4601 4720 c100 0000" \
      -D baud=921600 --entries 1
check "c300 e000 4710 4010 4207 e000 25a0 8000 0206 e000 25a0 4011 25a0 4720 0000" \
      "2000 e00d 4710 0500 4207 e000 25a0 a000 0206 e000 25a0 050d 4601 4720 c100 0000" \
      -D baud=1843200
check "c300 4207 4010 e1ff e005 e003 8000 e1ff e005 0206 4011 e1ff e005 0000" \
      "2000 e0fd e000 4207 0500 e1ff e005 e003 a000 e1ff e005 0208 050e 4601 c100 0000" --lat 2
pass
