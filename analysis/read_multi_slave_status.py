import argparse
import time
from safe_rs485_probe import probe_read
import serial


def main():
    ap = argparse.ArgumentParser(description="Read-only status snapshot for multiple RS485 slaves")
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--slaves", default="1,2,3,4,5,6")
    ap.add_argument("--timeout", type=float, default=0.35)
    ap.add_argument("--delay", type=float, default=0.08)
    args = ap.parse_args()
    slaves = [int(x, 0) for x in args.slaves.split(',') if x.strip()]
    # Only previously identified status/input/runtime registers. No writes.
    targets = [0x0001, 0x0002, 0x000B, 0x0017, 0x0018, 0x001F,
               0x0020, 0x0021, 0x0024]
    with serial.Serial(args.port, args.baud, bytesize=8, parity=serial.PARITY_NONE,
                       stopbits=1, timeout=args.timeout) as ser:
        for slave in slaves:
            print(f"slave {slave}:")
            for reg in targets:
                resp, err = probe_read(ser, slave, reg, 1, args.timeout, quiet=True)
                if err:
                    print(f"  0x{reg:04X}: {err}")
                else:
                    data = resp[3:-2]
                    value = (data[0] << 8) | data[1] if len(data) >= 2 else None
                    print(f"  0x{reg:04X}: 0x{value:04X} ({value})")
                time.sleep(args.delay)
            print()


if __name__ == '__main__':
    main()
