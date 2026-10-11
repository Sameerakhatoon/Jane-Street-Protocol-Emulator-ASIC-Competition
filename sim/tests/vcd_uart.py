"""Decode 8N1 bytes on one 1-bit wire of a pins-only VCD. Prints them as hex.

usage: vcd_uart.py FILE WIRE BAUD
"""
import sys

path, wire, baud = sys.argv[1], sys.argv[2], int(sys.argv[3])
ident, t, edges = None, 0, []
for line in open(path):
    w = line.split()
    if not w:
        continue
    if w[0] == "$var" and w[4] == wire:
        ident = w[3]
    elif w[0].startswith("#"):
        t = int(w[0][1:])
    elif ident and len(w[0]) >= 2 and w[0][1:] == ident:
        edges.append((t, int(w[0][0])))


def level(at):
    v = 1
    for et, ev in edges:
        if et > at:
            break
        v = ev
    return v


bit = 1e9 / baud
out, i = [], 0
while i < len(edges):
    t0, v = edges[i]
    if v != 0:
        i += 1
        continue
    b = 0
    for k in range(8):
        b |= level(t0 + (1.5 + k) * bit) << k
    if level(t0 + 9.5 * bit) == 1:
        out.append(b)
    end = t0 + 9.5 * bit
    while i < len(edges) and edges[i][0] <= end:
        i += 1
print("".join(f"{b:02x}" for b in out))
