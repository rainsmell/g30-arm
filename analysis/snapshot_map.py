r"""Read-only full 2-channel register snapshot for the dual-axis joint-5/6 board.

Sends only function-0x03 reads. Saves JSON and prints non-zero registers so two
power-on states (e.g. before/after a manual power-off offset) can be compared.

Usage:
    python analysis\snapshot_map.py --slave 5 --tag baseline
    python analysis\snapshot_map.py --slave 5 --tag offset30
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import serial
except ImportError:
    serial = None

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_rs485_probe import probe_read  # noqa: E402

CHANNELS = {"A": 0x0000, "B": 0x0024}
CHANNEL_LEN = 0x24


def read_chunk(ser, slave, start, count, timeout):
    for _ in range(4):
        resp, err = probe_read(ser, slave, start, count, timeout, quiet=True)
        if err:
            time.sleep(0.06)
            continue
        data = resp[3:-2]
        if len(data) != count * 2:
            time.sleep(0.06)
            continue
        return [(data[i * 2] << 8) | data[i * 2 + 1] for i in range(count)]
    return None


def read_map(ser, slave, timeout, chunk=0x20):
    vals = {}
    start = 0x0000
    end = 0x0048
    while start < end:
        n = min(chunk, end - start)
        part = read_chunk(ser, slave, start, n, timeout)
        if part is None:
            return None
        for i, v in enumerate(part):
            vals[start + i] = v
        start += n
    return vals


def s16(v):
    return v - 0x10000 if v >= 0x8000 else v


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--slave", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=0.5)
    ap.add_argument("--tag", default="")
    ap.add_argument("--out", default="analysis/motion_logs")
    args = ap.parse_args()

    if serial is None:
        print("pyserial is not installed", file=sys.stderr)
        return 2

    tag = args.tag or datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    with serial.Serial(
        args.port, args.baud, bytesize=8, parity=serial.PARITY_NONE,
        stopbits=1, timeout=args.timeout,
    ) as ser:
        vals = read_map(ser, args.slave, args.timeout)

    if vals is None:
        print("read failed")
        return 1

    record = {
        "timestamp_local": datetime.now().isoformat(timespec="seconds"),
        "slave": args.slave,
        "tag": tag,
        "read_only": True,
        "registers": {f"0x{k:04X}": v for k, v in sorted(vals.items())},
    }
    path = outdir / f"snapshot_slave{args.slave}_{tag}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    for name, base in CHANNELS.items():
        print(f"channel {name} (base 0x{base:04X}):")
        for off in range(CHANNEL_LEN):
            v = vals.get(base + off)
            if v:
                print(f"  +0x{off:02X}  reg 0x{base + off:04X} = 0x{v:04X} ({s16(v)})")
    print("saved:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
