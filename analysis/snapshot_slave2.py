"""Read five slave-2 snapshots on COM8. No register writes or motion commands."""
import json
import struct
import time
from datetime import datetime
from pathlib import Path

import serial
from safe_rs485_probe import build_read_holding, probe_read


def main():
    output = Path(__file__).resolve().parent / "motion_logs" / (
        "slave2_readonly_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
    result = {"port": "COM8", "baud": 115200, "slave": 2,
              "read_only": True, "start": 0, "count": 38,
              "tx": build_read_holding(2, 0, 38).hex(" ").upper(), "samples": []}
    try:
        with serial.Serial("COM8", 115200, timeout=0.5, write_timeout=0.5) as ser:
            for index in range(5):
                response, error = probe_read(ser, 2, 0, 38, 0.5, quiet=True)
                if not error and (len(response) != 81 or response[:3] != bytes((2, 3, 76))):
                    error = "Unexpected response function or length"
                entry = {"time": datetime.now().isoformat(timespec="milliseconds"),
                         "rx": (response or b"").hex(" ").upper(), "error": error}
                if not error:
                    words = struct.unpack(">38H", response[3:-2])
                    entry["registers"] = {f"r{reg:04X}": value for reg, value in enumerate(words)}
                    print(f"Snapshot {index}: " + " ".join(f"{reg:04X}={value:04X}" for reg, value in enumerate(words)))
                else:
                    print(f"Snapshot {index}: {error}")
                result["samples"].append(entry)
                if index < 4:
                    time.sleep(0.5)
    except Exception as exc:
        result["error"] = str(exc)
        print(f"Read failed: {exc}")
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print("JSON:", output)
    return int(bool(result.get("error")) or any(s["error"] for s in result["samples"]))


if __name__ == "__main__":
    raise SystemExit(main())
