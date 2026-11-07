#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Runs as uid 0 inside: unshare --user --map-root-user --net
# usage: tap_inner.sh SIM_BINARY PCAP_FILE
set -e
ip tuntap add dev tap0 mode tap
ip addr add 10.0.0.1/24 dev tap0
ip link set tap0 up
"$1" --tap tap0 --pcap "$2" &
sim=$!
sleep 0.5
ping -c 3 -i 0.3 -W 2 10.0.0.2
ip neigh show dev tap0
kill $sim
wait $sim || true
