"""Offline comparison of two captured slave-4 drive parameter settings."""
import csv
import json
import os
from pathlib import Path

import numpy as np

logs = Path(__file__).resolve().parent / "motion_logs"
os.environ.setdefault("MPLCONFIGDIR", str(logs.parent / ".matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def signed(value):
    number = int(value)
    return number - 65536 if number >= 32768 else number


fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
result = {}
for suffix, label in [("32bit", "5250 (baseline)"), ("halfdrive", "2625 (half parameter)")]:
    path = logs / f"slave4_initial90_{suffix}"
    with path.with_suffix(".csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    start = next(c["elapsed_s"] for c in metadata["commands"] if c["label"] == "CONTRACT_START")
    valid = [r for r in rows if r["r0000"] and not r["read_errors"]]
    initial = signed(valid[0]["r0001"])
    bottom = min(signed(r["r0001"]) for r in valid)
    stats = {"valid": len(valid), "attempts": len(rows), "excursion": initial - bottom}
    for phase in ("contract", "expand"):
        phase_rows = [r for r in valid if r["phase"] == phase]
        middle = [r for r in phase_rows if
                  0.2 < (signed(r["r0001"]) - bottom) / (initial - bottom) < 0.8
                  and abs(signed(r["r0000"])) > 100]
        stats[phase] = {
            "readback_r001F": sorted({int(r["r001F"]) for r in phase_rows}),
            "middle_speed_median": float(np.median([signed(r["r0000"]) for r in middle])),
            "middle_position_rate": float(np.polyfit(
                [float(r["elapsed_s"]) for r in middle],
                [signed(r["r0001"]) for r in middle], 1)[0]),
        }
    result[suffix] = stats
    for ax, key in zip(axes, ("r0000", "r0001", "r001F")):
        ax.plot([float(r["elapsed_s"]) - start for r in rows],
                [signed(r[key]) if r[key] else float("nan") for r in rows], label=label)
        ax.set_ylabel(key)
        ax.grid(alpha=0.3)
        ax.legend()
axes[-1].set_xlabel("Seconds from contraction response")
fig.suptitle("Slave 4: halved parameter, nearly unchanged speed")
fig.tight_layout()
fig.savefig(logs / "slave4_halfdrive_comparison.png", dpi=140)
plt.close(fig)
(logs / "slave4_halfdrive_comparison.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
