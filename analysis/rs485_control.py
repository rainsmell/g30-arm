import argparse
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


def build_frame(addr: int, func: int, payload: bytes) -> bytes:
    body = bytes([addr & 0xFF, func & 0xFF]) + payload
    crc = crc16_modbus(body)
    return body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def read_exact(ser, n: int, timeout_s: float) -> bytes:
    end = time.time() + timeout_s
    buf = bytearray()
    while len(buf) < n and time.time() < end:
        chunk = ser.read(n - len(buf))
        if chunk:
            buf.extend(chunk)
        else:
            time.sleep(0.01)
    return bytes(buf)


def write_single(ser, slave: int, func: int, reg: int, value: int, timeout_s: float):
    frame = build_frame(
        slave,
        func,
        bytes([
            (reg >> 8) & 0xFF,
            reg & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF,
        ]),
    )
    ser.reset_input_buffer()
    ser.write(frame)
    ser.flush()
    resp = read_exact(ser, 8, timeout_s)
    return frame, resp


def parse_hex(x: str) -> int:
    return int(x, 0)


def main():
    ap = argparse.ArgumentParser(description="Minimal RS485 register control helper")
    ap.add_argument("--port", default="COM4")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--slave", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=0.3)
    ap.add_argument("--func", type=parse_hex, default=0x06)
    ap.add_argument("--reg", type=parse_hex, required=True)
    ap.add_argument("--value", type=parse_hex, required=True)
    args = ap.parse_args()

    if serial is None:
        print("pyserial not installed", file=sys.stderr)
        sys.exit(1)

    with serial.Serial(args.port, args.baud, bytesize=8, parity=serial.PARITY_NONE, stopbits=1, timeout=args.timeout) as ser:
        tx, rx = write_single(ser, args.slave, args.func, args.reg, args.value, args.timeout)
        print("TX:", tx.hex(" "))
        print("RX:", rx.hex(" ") if rx else "<timeout>")


if __name__ == "__main__":
    main()
