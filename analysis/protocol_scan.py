from pathlib import Path
import re
import struct

from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB


BASE = 0x08000000
FW = Path("gd32_firmware.bin").read_bytes()


def scan_strings():
    print("== strings ==")
    for m in re.finditer(rb"[ -~]{4,}", FW):
        s = m.group().decode("ascii", "ignore")
        if any(k in s.lower() for k in ("crc", "code", "motor", "uart", "usart", "boot", "version")):
            print(f"{m.start():08x}: {s}")


def scan_addrs():
    print("\n== peripheral refs ==")
    addrs = {
        "USART0": 0x40013800,
        "USART1": 0x40004400,
        "USART2": 0x40004800,
        "GPIOA": 0x40010800,
        "GPIOB": 0x40010C00,
    }
    for name, addr in addrs.items():
        pat = struct.pack("<I", addr)
        hits = [i for i in range(0, len(FW) - 3) if FW[i : i + 4] == pat]
        if hits:
            print(name, ", ".join(f"{h:08x}" for h in hits))


def disasm(start, end):
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    for ins in md.disasm(FW[start:end], BASE + start):
        print(f"{ins.address:08x}: {ins.mnemonic:<8} {ins.op_str}")


if __name__ == "__main__":
    scan_strings()
    scan_addrs()
    print("\n== disasm 0x9080..0x9358 ==")
    disasm(0x9080, 0x9358)
