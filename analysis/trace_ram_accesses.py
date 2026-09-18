from pathlib import Path
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
from capstone.arm import *

BASE = 0x08000000
FW = Path("gd32_firmware.bin").read_bytes()
RAM0, RAM1 = 0x20000100, 0x200002c0

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True


def u32(addr):
    off = addr - BASE
    if 0 <= off <= len(FW) - 4:
        return int.from_bytes(FW[off:off+4], "little")
    return None


def is_ret(ins):
    return (
        ins.id == ARM_INS_BX and ins.operands and ins.operands[0].reg == ARM_REG_LR
    ) or (
        ins.id == ARM_INS_POP and any(op.type == ARM_OP_REG and op.reg == ARM_REG_PC for op in ins.operands)
    )


def collect_starts():
    starts = {0x08009e68, 0x08008c30, 0x08008ffc, 0x0800adbc, 0x0800af88}
    # crude but useful: any immediate call target
    for s in range(BASE, BASE + len(FW), 2):
        code = FW[s-BASE:s-BASE+4]
        insns = list(md.disasm(code, s, count=1))
        if not insns:
            continue
        ins = insns[0]
        if ins.id in (ARM_INS_BL, ARM_INS_BLX) and ins.operands and ins.operands[0].type == ARM_OP_IMM:
            t = ins.operands[0].imm
            if BASE <= t < BASE + len(FW):
                starts.add(t)
    return sorted(starts)


def trace(start):
    regs = {}
    out = []
    off = start - BASE
    for n, ins in enumerate(md.disasm(FW[off:min(len(FW), off+0x500)], start)):
        if n > 250:
            break

        if ins.id in (ARM_INS_LDR, ARM_INS_LDRH, ARM_INS_LDRB, ARM_INS_LDRSH, ARM_INS_LDRSB,
                      ARM_INS_STR, ARM_INS_STRH, ARM_INS_STRB) and len(ins.operands) >= 2:
            memop = ins.operands[1]
            if memop.type == ARM_OP_MEM:
                basev = regs.get(memop.mem.base)
                if basev is not None:
                    addr = basev + memop.mem.disp
                    if RAM0 <= addr < RAM1:
                        out.append((ins.address, ins.mnemonic, ins.op_str, addr))

        # update tracking
        if ins.id == ARM_INS_LDR and len(ins.operands) >= 2 and ins.operands[0].type == ARM_OP_REG:
            dst = ins.operands[0].reg
            src = ins.operands[1]
            if src.type == ARM_OP_MEM and src.mem.base == ARM_REG_PC:
                lit = ((ins.address + 4) & ~3) + src.mem.disp
                val = u32(lit)
                if val is not None and RAM0 <= val < RAM1:
                    regs[dst] = val
                else:
                    regs.pop(dst, None)
            else:
                regs.pop(dst, None)
        elif ins.id == ARM_INS_MOV and len(ins.operands) >= 2 and ins.operands[0].type == ARM_OP_REG:
            dst = ins.operands[0].reg
            src = ins.operands[1]
            if src.type == ARM_OP_REG and src.reg in regs:
                regs[dst] = regs[src.reg]
            elif src.type == ARM_OP_IMM and RAM0 <= src.imm < RAM1:
                regs[dst] = src.imm
            else:
                regs.pop(dst, None)
        elif ins.id in (ARM_INS_ADD, ARM_INS_SUB) and len(ins.operands) >= 2 and ins.operands[0].type == ARM_OP_REG:
            dst = ins.operands[0].reg
            if len(ins.operands) == 2:
                basev = regs.get(dst)
                op = ins.operands[1]
                immv = op.imm if op.type == ARM_OP_IMM else None
            else:
                a, b = ins.operands[1], ins.operands[2]
                basev = regs.get(a.reg) if a.type == ARM_OP_REG else (a.imm if a.type == ARM_OP_IMM else None)
                immv = b.imm if b.type == ARM_OP_IMM else None
            if basev is not None and immv is not None:
                regs[dst] = basev + (immv if ins.id == ARM_INS_ADD else -immv)
            else:
                regs.pop(dst, None)
        elif ins.id in (ARM_INS_BL, ARM_INS_BLX):
            for r in [ARM_REG_R0, ARM_REG_R1, ARM_REG_R2, ARM_REG_R3, ARM_REG_R12]:
                regs.pop(r, None)

        if is_ret(ins):
            break
    return out


def main():
    for s in collect_starts():
        rows = trace(s)
        rows = [r for r in rows if 0x20000118 <= r[3] < 0x200001a0]
        if rows:
            print(f"## {s:08X}")
            for addr, m, ops, ram in rows:
                print(f"{addr:08X}: {m:<7} {ops:<32} ; {ram:08X} rel_state={ram-0x20000118:+#x} rel_regs={ram-0x20000158:+#x}")
            print()


if __name__ == "__main__":
    main()
