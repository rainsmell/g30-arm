from pathlib import Path
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
from capstone.arm import *

BASE = 0x08000000
FW = Path("gd32_firmware.bin").read_bytes()

STATE = 0x20000118
REGS = 0x20000158

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True


def u32(addr):
    off = addr - BASE
    if off < 0 or off + 4 > len(FW):
        return None
    return int.from_bytes(FW[off:off + 4], "little")


def regname(r):
    return md.reg_name(r)


def pc_lit(ins, mem):
    lit = ((ins.address + 4) & ~3) + mem.disp
    return lit


def all_call_targets():
    targets = set()
    for ins in md.disasm(FW, BASE):
        if ins.id in (ARM_INS_BL, ARM_INS_BLX, ARM_INS_B):
            if ins.operands and ins.operands[0].type == ARM_OP_IMM:
                t = ins.operands[0].imm
                if BASE <= t < BASE + len(FW):
                    targets.add(t)
    # known manually identified entries
    targets.update([0x08008c30, 0x08008ffc, 0x0800adbc, 0x0800af88, 0x0800b004])
    return sorted(targets)


def is_ret(ins):
    if ins.id == ARM_INS_BX and ins.operands and ins.operands[0].reg == ARM_REG_LR:
        return True
    if ins.id == ARM_INS_POP and any(op.type == ARM_OP_REG and op.reg == ARM_REG_PC for op in ins.operands):
        return True
    return False


def trace_func(start, max_ins=220):
    off = start - BASE
    if off < 0 or off >= len(FW):
        return []
    regs = {}
    out = []
    for n, ins in enumerate(md.disasm(FW[off: min(len(FW), off + 0x500)], start)):
        if n >= max_ins:
            break

        def val_of(op):
            if op.type == ARM_OP_REG:
                return regs.get(op.reg)
            if op.type == ARM_OP_IMM:
                return op.imm
            return None

        # Log memory accesses through tracked base regs
        if ins.id in (ARM_INS_LDR, ARM_INS_LDRB, ARM_INS_LDRH, ARM_INS_LDRSB, ARM_INS_LDRSH,
                      ARM_INS_STR, ARM_INS_STRB, ARM_INS_STRH):
            if len(ins.operands) >= 2 and ins.operands[1].type == ARM_OP_MEM:
                mem = ins.operands[1].mem
                base = regs.get(mem.base)
                if base is not None:
                    addr = base + mem.disp
                    if STATE <= addr < STATE + 0x80 or REGS <= addr < REGS + 0xa0:
                        region = "STATE" if STATE <= addr < STATE + 0x80 else "REGS"
                        rel = addr - (STATE if region == "STATE" else REGS)
                        out.append((ins.address, ins.mnemonic, ins.op_str, region, rel))

        # Track simple constant/data-pointer propagation
        if ins.id == ARM_INS_LDR and len(ins.operands) >= 2 and ins.operands[0].type == ARM_OP_REG:
            dst = ins.operands[0].reg
            src = ins.operands[1]
            if src.type == ARM_OP_MEM and src.mem.base == ARM_REG_PC:
                lit = pc_lit(ins, src.mem)
                val = u32(lit)
                if val in (STATE, REGS):
                    regs[dst] = val
                else:
                    regs.pop(dst, None)
            else:
                regs.pop(dst, None)
        elif ins.id == ARM_INS_MOV and len(ins.operands) >= 2 and ins.operands[0].type == ARM_OP_REG:
            dst = ins.operands[0].reg
            v = val_of(ins.operands[1])
            if v is not None:
                regs[dst] = v
            else:
                regs.pop(dst, None)
        elif ins.id in (ARM_INS_ADD, ARM_INS_SUB) and len(ins.operands) >= 2 and ins.operands[0].type == ARM_OP_REG:
            dst = ins.operands[0].reg
            if len(ins.operands) == 2:
                basev = regs.get(dst)
                immv = val_of(ins.operands[1])
            else:
                basev = val_of(ins.operands[1])
                immv = val_of(ins.operands[2])
            if basev is not None and immv is not None:
                regs[dst] = basev + (immv if ins.id == ARM_INS_ADD else -immv)
            else:
                regs.pop(dst, None)
        elif ins.id in (ARM_INS_BL, ARM_INS_BLX):
            # caller-saved regs become unknown
            for r in [ARM_REG_R0, ARM_REG_R1, ARM_REG_R2, ARM_REG_R3, ARM_REG_R12]:
                regs.pop(r, None)

        if is_ret(ins):
            break
    return out


def main():
    for f in all_call_targets():
        rows = trace_func(f)
        if not rows:
            continue
        interesting = [
            r for r in rows
            if r[3] == "STATE" and r[4] in (0x0e, 0x10, 0x12, 0x14, 0x18, 0x1c, 0x20, 0x24, 0x28, 0x2c)
            or r[3] == "REGS" and r[4] in (0x02, 0x16, 0x26, 0x28, 0x2e, 0x3e, 0x40, 0x42, 0x48)
        ]
        if interesting:
            print(f"## function-ish {f:08X}")
            for addr, mnem, ops, region, rel in rows:
                mark = "*" if (addr, mnem, ops, region, rel) in interesting else " "
                print(f"{mark} {addr:08X}: {mnem:<7} {ops:<32} ; {region}+0x{rel:02X}")
            print()


if __name__ == "__main__":
    main()
