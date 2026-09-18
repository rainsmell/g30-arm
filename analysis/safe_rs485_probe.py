import argparse
import binascii
import sys
import time

try:
    import serial
except ImportError:
    serial = None


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


def build_read_holding(slave: int, start: int, count: int) -> bytes:
    frame = bytes([
        slave & 0xFF,
        0x03,
        (start >> 8) & 0xFF,
        start & 0xFF,
        (count >> 8) & 0xFF,
        count & 0xFF,
    ])
    crc = crc16_modbus(frame)
    return frame + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def read_exact(ser, n: int, timeout_s: float):
    end = time.time() + timeout_s
    buf = bytearray()
    while len(buf) < n and time.time() < end:
        chunk = ser.read(n - len(buf))
        if chunk:
            buf.extend(chunk)
        else:
            time.sleep(0.01)
    return bytes(buf)


def probe_read(ser, slave: int, start: int, count: int, timeout_s: float, quiet: bool = False):
    req = build_read_holding(slave, start, count)
    ser.reset_input_buffer()
    ser.write(req)
    ser.flush()

    hdr = read_exact(ser, 3, timeout_s)
    if len(hdr) < 3:
        return None, "timeout waiting for header"

    addr, func, third = hdr
    if addr != slave:
        return hdr, f"unexpected addr 0x{addr:02X}"

    if func & 0x80:
        rest = read_exact(ser, 2, timeout_s)
        resp = hdr + rest
        return resp, f"exception 0x{third:02X}"

    bytecount = third
    rest = read_exact(ser, bytecount + 2, timeout_s)
    resp = hdr + rest
    if len(resp) < 5:
        return resp, "short response"

    data = resp[3:-2]
    crc_rx = resp[-2] | (resp[-1] << 8)
    crc_calc = crc16_modbus(resp[:-2])
    if crc_rx != crc_calc:
        return resp, f"crc mismatch rx=0x{crc_rx:04X} calc=0x{crc_calc:04X}"

    words = []
    for i in range(0, len(data), 2):
        if i + 1 < len(data):
            words.append((data[i] << 8) | data[i + 1])
    if not quiet:
        print(f"reg 0x{start:04X} count {count} -> " + " ".join(f"0x{w:04X}" for w in words))
    return resp, None


def main():
    ap = argparse.ArgumentParser(description="Safe read-only RS485 probe for the arm joint MCU")
    ap.add_argument("--port", default="COM4")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--slave", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=0.25)
    ap.add_argument("--delay", type=float, default=0.08)
    ap.add_argument("--start", type=lambda x: int(x, 0))
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--scan", action="store_true", help="scan a short safe range 0x0000..0x0020")
    ap.add_argument("--scan-slaves", action="store_true", help="scan slave ids 1..32 with one safe read")
    args = ap.parse_args()

    if serial is None:
        print("pyserial not installed. Run: pip install pyserial", file=sys.stderr)
        sys.exit(1)

    with serial.Serial(args.port, args.baud, bytesize=8, parity=serial.PARITY_NONE, stopbits=1, timeout=args.timeout) as ser:
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        if args.scan:
            # Small, read-only sweep. No writes, no special address 0xAA.
            for start in range(0x0000, 0x0021, 1):
                _, err = probe_read(ser, args.slave, start, 1, args.timeout, quiet=False)
                if err:
                    print(f"reg 0x{start:04X}: {err}")
                time.sleep(args.delay)
            return

        if args.scan_slaves:
            for sid in range(1, 33):
                _, err = probe_read(ser, sid, 0x0017, 1, args.timeout, quiet=True)
                if err:
                    print(f"slave {sid}: {err}")
                else:
                    print(f"slave {sid}: OK")
                time.sleep(args.delay)
            return

        if args.start is None:
            # Conservative default reads: known candidate status registers only.
            targets = [0x0017, 0x0019, 0x001B, 0x001C]
            for start in targets:
                _, err = probe_read(ser, args.slave, start, args.count, args.timeout, quiet=False)
                if err:
                    print(f"reg 0x{start:04X}: {err}")
                time.sleep(args.delay)
            return

        _, err = probe_read(ser, args.slave, args.start, args.count, args.timeout, quiet=False)
        if err:
            print(err)


if __name__ == "__main__":
    main()
