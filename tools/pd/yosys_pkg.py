#!/usr/bin/env python3
# Yosys-readable copy of a generated package (Yosys has no `return` and no
# enum constants inside package functions): `return X;` becomes `<function> = X;`
# and each typedef enum becomes a plain type with localparam values.
import re, sys
# usage: yosys_pkg.py <package.sv> <copy.sv>
src, dst = sys.argv[1], sys.argv[2]
out, fn = [], None
for line in open(src):
    m = re.match(r'(\s*)typedef enum (logic \[\d+:0\])\s*\{([^}]*)\}\s*(\w+);', line)
    if m:
        ind, ty, names, tn = m.groups()
        vals = [n.strip() for n in names.split(',')]
        line = '%stypedef %s %s;\n%slocalparam %s %s;\n' % (ind, ty, tn, ind, tn,
               ', '.join('%s = %d' % (n, i) for i, n in enumerate(vals)))
    m = re.search(r'function\s+automatic\s+.*?(\w+)\s*\(', line)
    if m: fn = m.group(1)
    if 'endfunction' in line: fn = None
    if fn: line = re.sub(r'\breturn\b', fn + ' =', line)
    out.append(line)
open(dst, 'w').write(''.join(out))
