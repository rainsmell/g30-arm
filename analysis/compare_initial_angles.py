"""Compare captured startup-angle experiments without accessing serial hardware."""
import csv
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "motion_logs"
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

def signed(value):
    value = int(value)
    return value - 65536 if value >= 32768 else value

fig, axes = plt.subplots(4, 2, figsize=(14, 11), sharex="col")
metrics = {}
for angle, color in [(180, "#0072B2"), (90, "#D55E00")]:
    stem = LOGS / f"slave4_initial{angle}"
    with stem.with_suffix(".csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    meta = json.loads(stem.with_suffix(".json").read_text(encoding="utf-8"))
    valid = [r for r in rows if r["r0001"] and not r["read_errors"]]
    commands = {c["label"]: c["elapsed_s"] for c in meta["commands"]}
    contract = [r for r in valid if r["phase"] == "contract"]
    expand = [r for r in valid if r["phase"] == "expand"]
    bottom = min(signed(r["r0001"]) for r in contract)
    initial = signed(valid[0]["r0001"])
    hit = next(r for r in contract if signed(r["r0001"]) == bottom)
    # Require a 10-count departure to avoid calling one-count noise motion.
    leave = next(r for r in expand if signed(r["r0001"]) >= bottom + 10)
    returned = next(r for r in expand if abs(signed(r["r0001"]) - initial) <= 5)
    transitions = []
    previous = None
    for r in valid:
        if r["r0018"] != previous:
            transitions.append([float(r["elapsed_s"]), int(r["r0018"])])
            previous = r["r0018"]
    metrics[str(angle)] = {
        "attempts": len(rows), "valid": len(valid), "initial": initial,
        "contract_endpoint": bottom, "excursion": initial - bottom,
        "final": signed(valid[-1]["r0001"]),
        "endpoint_time_from_contract": float(hit["elapsed_s"]) - commands["CONTRACT_START"],
        "departure_delay_from_expand_10counts": float(leave["elapsed_s"]) - commands["EXPAND_START"],
        "return_time_from_expand_within5counts": float(returned["elapsed_s"]) - commands["EXPAND_START"],
        "r0018_transitions_elapsed": transitions,
        "r0002_r0004_equal": all(r["r0002"] == r["r0004"] for r in valid),
        "r0003_initial_peak_final": [signed(valid[0]["r0003"]), max(signed(r["r0003"]) for r in valid), signed(valid[-1]["r0003"])],
    }
    for column, anchor in enumerate(["CONTRACT_START", "EXPAND_START"]):
        for row_index, key in enumerate(["r0001", "r0002", "r0003", "r0018"]):
            ax = axes[row_index][column]
            xs = [float(r["elapsed_s"]) - commands[anchor] for r in rows]
            ys = [signed(r[key]) if r[key] else float("nan") for r in rows]
            ax.plot(xs, ys, color=color, label=f"Initial {angle} deg", linewidth=1.3)
            ax.set_ylabel(key + " (int16)" if key != "r0018" else key)
            ax.grid(alpha=0.25)
            ax.axvline(0, color="gray", linestyle=":")
            ax.set_xlim((-0.5, 28.5) if column == 0 else (-0.5, 18.5))
            ax.legend(fontsize=8)
        axes[-1][column].set_xlabel(f"Seconds from {anchor} response")
axes[0][0].set_title("Aligned to contraction response")
axes[0][1].set_title("Aligned to expansion response")
fig.suptitle("Slave 4: startup angle comparison (register meanings provisional)")
fig.tight_layout()
fig.savefig(LOGS / "slave4_initial_angle_comparison.png", dpi=140)
plt.close(fig)
metrics["excursion_ratio_180_to_90"] = metrics["180"]["excursion"] / metrics["90"]["excursion"]
output = LOGS / "slave4_initial_angle_comparison.json"
output.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
print(json.dumps(metrics, indent=2))
