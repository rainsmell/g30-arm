"""Offline position-width and velocity-candidate checks for slave 4 captures."""
import csv
import json
import os
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "motion_logs"
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def signed(value):
    value = int(value)
    return value - 65536 if value >= 32768 else value


fig, axes = plt.subplots(3, 2, figsize=(14, 10), sharex="col")
summary = {}
for col, angle in enumerate((90, 180)):
    stem = LOGS / f"slave4_initial{angle}_32bit"
    with stem.with_suffix(".csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    meta = json.loads(stem.with_suffix(".json").read_text(encoding="utf-8"))
    valid = [r for r in rows if r["r0000"] and not r["read_errors"]]
    t = np.array([float(r["elapsed_s"]) for r in valid])
    pos = np.array([signed(r["r0001"]) for r in valid])
    vel = np.array([signed(r["r0000"]) for r in valid])
    dt = np.diff(t)
    # Exclude timeout gaps. Average adjacent speed readings over each interval.
    mask = (dt > 0) & (dt <= 0.2)
    rate = np.diff(pos)[mask] / dt[mask]
    midpoint_vel = ((vel[:-1] + vel[1:]) / 2)[mask]
    fit = np.polyfit(midpoint_vel, rate, 1)
    phases = {}
    for phase in ("contract", "expand", "post"):
        idx = [i for i, r in enumerate(valid) if r["phase"] == phase]
        moving = [i for i in idx if abs(vel[i]) > 650]
        phases[phase] = {
            "r0000_min_max": [int(min(vel[idx])), int(max(vel[idx]))],
            "r0000_moving_median": float(np.median(vel[moving])) if moving else None,
        }
    matches = sum(int(r["r0000"]) == (65535 if int(r["r0001"]) >= 32768 else 0) for r in valid)
    summary[str(angle)] = {
        "attempts": len(rows), "valid": len(valid),
        "position_initial_min_final": [int(pos[0]), int(min(pos)), int(pos[-1])],
        "excursion_counts": int(pos[0] - min(pos)),
        "sign_extension_matches": matches,
        "r0000_rate_correlation": float(np.corrcoef(midpoint_vel, rate)[0, 1]),
        "position_rate_fit_slope_intercept": fit.tolist(),
        "phases": phases,
        "final_inhibit_readback": meta.get("final_inhibit_last_readback"),
    }
    x = [float(r["elapsed_s"]) for r in rows]
    for ax, key in zip(axes[:, col], ("r0001", "r0000", "r0002")):
        y = [signed(r[key]) if r[key] else float("nan") for r in rows]
        ax.plot(x, y, label=f"{key} signed int16", color="#0072B2")
        for cmd in meta["commands"]:
            ax.axvline(cmd["elapsed_s"], color="gray", alpha=0.4, linestyle=":")
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8)
    axes[1, col].plot(((t[1:] + t[:-1]) / 2)[mask], rate / fit[0],
                      label="position derivative / fitted scale", color="#D55E00", alpha=0.65)
    axes[1, col].legend(fontsize=8)
    axes[0, col].set_title(f"Initial {angle} deg")
    axes[-1, col].set_xlabel("Elapsed seconds")
summary["excursion_ratio_180_to_90"] = summary["180"]["excursion_counts"] / summary["90"]["excursion_counts"]
fig.suptitle("Slave 4: separate position and velocity candidates")
fig.tight_layout()
fig.savefig(LOGS / "slave4_position_width_review.png", dpi=140)
plt.close(fig)
(LOGS / "slave4_position_width_review.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(json.dumps(summary, indent=2))
