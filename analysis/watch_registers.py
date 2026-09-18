r"""Read-only register watcher for one RS485 slave. Sends no writes.

Use it to find which register tracks manual joint movement, or to compare a
joint's register map with the known 3/4 layout.

Example (watch slave 5 for 30 s at 10 Hz, registers 0x0000..0x0024):

    python analysis\watch_registers.py --slave 5 --seconds 30 --hz 10

Then slowly rotate the wrist by hand while it runs. Any register that changes
is a position/feedback candidate. The tool only issues function-0x03 reads.
"""

from __future__ import annotations

import argparse
import csv
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


def read_chunk(ser, slave, start, count, timeout):
    for _ in range(3):
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


def read_block(ser, slave, start, count, timeout, chunk=0x20):
    """Read [start, start+count) in Modbus-sized chunks."""
    out = []
    reg = start
    end = start + count
    while reg < end:
        n = min(chunk, end - reg)
        vals = read_chunk(ser, slave, reg, n, timeout)
        if vals is None:
            return None
        out.extend(vals)
        reg += n
    return out


def s16(v):
    return v - 0x10000 if v is not None and v >= 0x8000 else v


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--slave", type=int, default=5)
    ap.add_argument("--start", type=lambda x: int(x, 0), default=0x0000)
    ap.add_argument("--count", type=int, default=0x25)
    ap.add_argument("--full", action="store_true",
                    help="watch 0x0000..0x00FF instead of the default map")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--hz", type=float, default=10.0)
    ap.add_argument("--timeout", type=float, default=0.4)
    ap.add_argument("--out", default="analysis/motion_logs")
    args = ap.parse_args()

    if serial is None:
        print("pyserial is not installed", file=sys.stderr)
        return 2

    if args.full:
        args.start = 0x0000
        args.count = 0x0100

    regs = list(range(args.start, args.start + args.count))
    tag = f"watch_slave{args.slave}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    csv_path = outdir / f"{tag}.csv"

    interval = 1.0 / args.hz
    samples = []
    baseline = None
    changed = set()

    print(f"read-only watch of slave {args.slave} on {args.port} for {args.seconds:g}s")
    print("No writes are sent. Rotate the joint by hand now.")

    t0 = time.monotonic()
    try:
        with serial.Serial(
            args.port, args.baud, bytesize=8, parity=serial.PARITY_NONE,
            stopbits=1, timeout=args.timeout,
        ) as ser:
            next_t = t0
            while time.monotonic() - t0 < args.seconds:
                now = time.monotonic()
                if now < next_t:
                    time.sleep(next_t - now)
                vals = read_block(ser, args.slave, args.start, args.count, args.timeout)
                elapsed = round(time.monotonic() - t0, 3)
                if vals is None:
                    print(f"  {elapsed:6.2f}s  read timeout")
                else:
                    if baseline is None:
                        baseline = list(vals)
                    else:
                        for i, v in enumerate(vals):
                            if v != baseline[i]:
                                changed.add(regs[i])
                    samples.append((elapsed, vals))
                next_t += interval
                if next_t < time.monotonic():
                    next_t = time.monotonic()
    except KeyboardInterrupt:
        print("interrupted")

    if not samples:
        print("no valid samples")
        return 1

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["elapsed_s", *[f"r{r:04X}" for r in regs]])
        for elapsed, vals in samples:
            writer.writerow([elapsed, *vals])

    print()
    print("registers that changed relative to first sample:")
    if changed:
        for r in sorted(changed):
            series = [s16(v[regs.index(r)]) for _, v in samples]
            print(f"  0x{r:04X}: min={min(series)} max={max(series)}")
    else:
        print("  (none - nothing tracked hand movement)")
    print("CSV:", csv_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
