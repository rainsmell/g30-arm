import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import serial
from safe_rs485_probe import probe_read

STATUS = [
    0x0001, 0x0002, 0x000B, 0x0017, 0x0018, 0x001F,
    0x0020, 0x0021, 0x0024,
]
CONFIG = [0x0013, 0x0014, 0x0019, 0x001A, 0x001B, 0x001C, 0x001D, 0x001E, 0x0022, 0x0023, 0x0025]


def read_one(ser, slave, reg, timeout):
    resp, err = probe_read(ser, slave, reg, 1, timeout, quiet=True)
    if err:
        return None, err
    data = resp[3:-2]
    if len(data) != 2:
        return None, f"unexpected data length {len(data)}"
    return (data[0] << 8) | data[1], None


def main():
    ap = argparse.ArgumentParser(description="Read-only multi-slave status/config snapshot")
    ap.add_argument('--port', default='COM8')
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--slaves', default='1,2,3,4,5,6')
    ap.add_argument('--timeout', type=float, default=0.35)
    ap.add_argument('--delay', type=float, default=0.08)
    ap.add_argument('--out', default='analysis/multi_slave_snapshot.json')
    args = ap.parse_args()
    slaves = [int(x, 0) for x in args.slaves.split(',') if x.strip()]
    result = {
        'timestamp_local': datetime.now().isoformat(timespec='seconds'),
        'port': args.port,
        'baud': args.baud,
        'read_only': True,
        'slaves': {},
    }
    with serial.Serial(args.port, args.baud, bytesize=8, parity=serial.PARITY_NONE,
                       stopbits=1, timeout=args.timeout) as ser:
        for slave in slaves:
            row = {}
            print(f'slave {slave}:')
            for group, regs in [('status', STATUS), ('config', CONFIG)]:
                row[group] = {}
                for reg in regs:
                    value, err = read_one(ser, slave, reg, args.timeout)
                    key = f'0x{reg:04X}'
                    if err:
                        row[group][key] = {'error': err}
                        print(f'  {group:6} {key}: {err}')
                    else:
                        row[group][key] = value
                        print(f'  {group:6} {key}: 0x{value:04X} ({value})')
                    time.sleep(args.delay)
            result['slaves'][str(slave)] = row
            print()
    Path(args.out).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'written: {args.out}')


if __name__ == '__main__':
    main()
