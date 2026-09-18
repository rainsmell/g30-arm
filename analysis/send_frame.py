import argparse
import sys
import time

try:
    import serial
except ImportError:
    serial = None


def parse_hex_frame(s: str) -> bytes:
    cleaned = (
        s.replace("0x", "")
        .replace("0X", "")
        .replace(",", " ")
        .replace("-", " ")
        .replace(":", " ")
    )
    parts = cleaned.split()
    if len(parts) == 1:
        compact = parts[0]
        if len(compact) % 2:
            raise ValueError("hex string length must be even")
        return bytes.fromhex(compact)
    return bytes(int(p, 16) & 0xFF for p in parts)


def main():
    ap = argparse.ArgumentParser(description="Send one raw hex frame to serial port")
    ap.add_argument("frame", help='hex frame, e.g. "01 10 00 0B 00 01 02 AA 11 19 87"')
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--timeout", type=float, default=0.5)
    ap.add_argument("--read", type=int, default=8, help="bytes to read after sending")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds to wait before reading")
    args = ap.parse_args()

    if serial is None:
        print("pyserial not installed", file=sys.stderr)
        sys.exit(1)

    frame = parse_hex_frame(args.frame)

    with serial.Serial(args.port, args.baud, bytesize=8, parity=serial.PARITY_NONE,
                       stopbits=1, timeout=args.timeout) as ser:
        ser.reset_input_buffer()
        ser.write(frame)
        ser.flush()
        if args.delay > 0:
            time.sleep(args.delay)
        resp = ser.read(args.read) if args.read > 0 else b""

    print("TX:", frame.hex(" "))
    print("RX:", resp.hex(" ") if resp else "<none>")


if __name__ == "__main__":
    main()
