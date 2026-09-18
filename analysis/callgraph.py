#!/usr/bin/env python
"""Scan a Thumb firmware blob and build a BL/BLX call graph.

Usage:
  python analysis/callgraph.py <binfile> [options]
    --app-base 0x08008000   scan start (default)
    --app-end  0x08020000   scan end (default)
    --callers  0x0800E46C   list callers of an address
    --callees  0x0800C3C6   list callees of an address
    --top N                 show N most-called functions (default 60)
"""
import sys
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN

def build(path, base, start, end):
    data = open(path, 'rb').read()
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)
    calls, callers = {}, {}
    off = start - base
    limit = end - base
    while off < limit - 4:
        found = False
        for ins in md.disasm(data[off:off+4], base + off):
            if ins.mnemonic in ('bl', 'blx') and ins.op_str.startswith('#0x'):
                t = int(ins.op_str[3:], 16)
                calls.setdefault(ins.address, set()).add(t)
                callers.setdefault(t, set()).add(ins.address)
            found = True
            off += ins.size
            break
        if not found:
            off += 2
    return calls, callers

def opt(args, name, default=None):
    if name in args:
        return args[args.index(name)+1]
    return default

def main():
    args = sys.argv[1:]
    path = args[0]
    base = 0x08000000
    start = int(opt(args, '--app-base', '0x08008000'), 0)
    end = int(opt(args, '--app-end', '0x08020000'), 0)
    calls, callers = build(path, base, start, end)
    if '--callers' in args:
        t = int(opt(args, '--callers'), 0)
        print(f'callers of 0x{t:08X}:')
        for c in sorted(callers.get(t, [])):
            print(f'  0x{c:08X}')
        return
    if '--callees' in args:
        c = int(opt(args, '--callees'), 0)
        print(f'callees of 0x{c:08X}:')
        for t in sorted(calls.get(c, [])):
            print(f'  0x{t:08X}')
        return
    n = int(opt(args, '--top', '60'))
    print(f'most-called targets ({len(callers)} total):')
    for t, cs in sorted(callers.items(), key=lambda kv: -len(kv[1]))[:n]:
        print(f'  0x{t:08X}: {len(cs)} call sites')

if __name__ == '__main__':
    main()
