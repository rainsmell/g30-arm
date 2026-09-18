#!/usr/bin/env python
"""Small Thumb disassembler helper for the GD32 firmware blobs.

Usage:
  python analysis/disasm.py <binfile> <base_hex> <start_hex> <len_hex> [--follow]

Prints an annotated listing.  BL/BLX targets and PC-relative literal loads are
annotated so RAM addresses and string references are easy to spot.
"""
import sys, struct
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN

def load(path, base):
    data = open(path, 'rb').read()
    return data, base

def lit_value(data, base, addr):
    off = addr - base
    if 0 <= off <= len(data) - 4:
        return struct.unpack_from('<I', data, off)[0]
    return None

def disasm(data, base, start, length, md):
    out = []
    for ins in md.disasm(data[start-base:start-base+length], start):
        note = ''
        m = ins.mnemonic
        op = ins.op_str
        if m in ('bl', 'blx') and op.startswith('#0x'):
            tgt = int(op[3:], 16)
            note = f'   ; -> 0x{tgt:08X}'
        if m.startswith('ldr') and 'pc,' in op:
            # ldr rX, [pc, #imm]
            try:
                imm = int(op.split('#')[-1].rstrip(']'), 0)
                litaddr = (ins.address + 4) & ~3
                litaddr += imm
                v = lit_value(data, base, litaddr)
                if v is not None:
                    tag = ''
                    if 0x08000000 <= v < 0x08020000:
                        tag = ' (flash ptr)'
                    elif 0x20000000 <= v < 0x20010000:
                        tag = ' (ram ptr)'
                    note = f'   ; [0x{litaddr:08X}] = 0x{v:08X}{tag}'
            except Exception:
                pass
        out.append(f'0x{ins.address:08X}: {ins.bytes.hex():<10} {m:<8} {op}{note}')
    return out

def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    follow = '--follow' in sys.argv
    path, base, start, length = args[0], int(args[1], 0), int(args[2], 0), int(args[3], 0)
    data, base = load(path, base)
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)
    md.detail = False
    for line in disasm(data, base, start, length, md):
        print(line)

if __name__ == '__main__':
    main()
