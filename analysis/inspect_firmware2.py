"""Reproducible static inventory and annotated Thumb listing. No hardware access."""
import hashlib
import json
import struct
from pathlib import Path
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
from disasm import disasm

root = Path(__file__).resolve().parent.parent
out = root / "analysis" / "firmware2"
out.mkdir(exist_ok=True)
blob = (root / "gd32_firmware_2.bin").read_bytes()
old = (root / "gd32_firmware.bin").read_bytes()
base = 0x08000000
metadata = {"file": "gd32_firmware_2.bin", "size": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(),
            "app_vectors": [hex(v) for v in struct.unpack_from("<64I", blob, 0x8000)],
            "metadata_1fc00": blob[0x1fc00:0x1fc30].hex(" "),
            "different_4k_pages": [hex(i) for i in range(0, len(blob), 4096)
                                   if blob[i:i+4096] != old[i:i+4096]]}
(out / "inventory.json").write_text(json.dumps(metadata, indent=2), encoding="ascii")
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
lines = []
offset = 0x8100
while offset < 0xf060:
    ins = next(md.disasm(blob[offset:offset+4], base+offset, count=1), None)
    if ins:
        lines.extend(disasm(blob, base, base+offset, ins.size, md))
        offset += ins.size
    else:
        lines.append(f"0x{base+offset:08X}: .hword 0x{int.from_bytes(blob[offset:offset+2], 'little'):04X}")
        offset += 2
(out / "application_linear.txt").write_text("\n".join(lines), encoding="ascii")
print(out)
