"""Single slave-2 motion trial; inhibit semantics are unverified on this joint.

Run: python analysis/test_joint2.py --run
Default without --run only displays the frames. Keep power cutoff accessible.
"""
import argparse
import json
import math
import time
from datetime import datetime
from pathlib import Path

import serial
from profile_motion_registers import beep, build_write10, send_write10


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM8")
    parser.add_argument("--seconds", type=float, default=2.0)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or not 0 < args.seconds <= 2:
        parser.error("--seconds must be positive and no greater than 2")
    print("Slave 2, 115200 8N1. Direction and physical stop are UNVERIFIED.")
    print("START:", build_write10(2, 0x000B, 0xAA11).hex(" ").upper())
    print("INHIBIT:", build_write10(2, 0x001F, 1).hex(" ").upper())
    print(f"Inhibit scheduled {args.seconds:g}s after start transmission begins.")
    if not args.run:
        print("Dry-run. Add --run to transmit. Be ready to cut power if motion continues.")
        return 0

    output = Path(__file__).resolve().parent / "motion_logs" / (
        "slave2_trial_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
    log = {"port": args.port, "slave": 2, "seconds": args.seconds,
           "physical_stop_confirmed": False, "commands": [], "errors": []}
    origin = time.monotonic()

    def command(ser, reg, value, label):
        entry = {"label": label, "elapsed_tx_begin": time.monotonic() - origin,
                 "tx": build_write10(2, reg, value).hex(" ").upper()}
        log["commands"].append(entry)
        response, error = send_write10(ser, 2, reg, value, 0.25)
        entry.update(rx=response.hex(" ").upper(), error=error,
                     elapsed_response=time.monotonic() - origin)
        print(f"{label}: RX {entry['rx'] or '<none>'}; error={error}", flush=True)
        if error:
            raise RuntimeError(f"{label}: {error}")

    try:
        with serial.Serial(args.port, 115200, timeout=0.05, write_timeout=0.25) as ser:
            beep()
            deadline = time.monotonic() + args.seconds
            # Keep the port open for cleanup even if the start response fails.
            try:
                command(ser, 0x000B, 0xAA11, "START_CANDIDATE")
                while time.monotonic() < deadline:
                    time.sleep(min(0.02, max(0, deadline - time.monotonic())))
            finally:
                command(ser, 0x001F, 1, "INHIBIT_CANDIDATE")
    except (Exception, KeyboardInterrupt) as exc:
        log["errors"].append(f"{type(exc).__name__}: {exc}")
        print("Trial error:", log["errors"][-1], flush=True)
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(log, indent=2), encoding="utf-8")
        print("Physical stop is NOT confirmed. Cut power if motion continues.", flush=True)
        print("JSON:", output)
    return 1 if log["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
