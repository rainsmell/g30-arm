import argparse


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def parse_int(s: str) -> int:
    return int(s, 0)


def fmt(data: bytes) -> str:
    return data.hex(" ").upper()


def main():
    ap = argparse.ArgumentParser(
        description="Build safe Modbus-like write-register frames for this firmware"
    )
    ap.add_argument("reg", type=parse_int, help="register, e.g. 0x001F")
    ap.add_argument("value", type=parse_int, help="16-bit value, e.g. 0x1482")
    ap.add_argument("--addr", type=parse_int, default=1)
    ap.add_argument("--func", type=parse_int, choices=(0x06, 0x10), default=0x10)
    args = ap.parse_args()

    reg = args.reg & 0xFFFF
    val = args.value & 0xFFFF
    addr = args.addr & 0xFF
    if args.func == 0x06:
        p = bytes([addr, 0x06, reg >> 8, reg & 0xFF, val >> 8, val & 0xFF])
    else:
        p = bytes([addr, 0x10, reg >> 8, reg & 0xFF, 0, 1, 2, val >> 8, val & 0xFF])

    crc = crc16_modbus(p)
    frame = p + bytes([crc & 0xFF, crc >> 8])
    print(fmt(frame))


if __name__ == "__main__":
    main()
