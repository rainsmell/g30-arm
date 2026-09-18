"""Offline signed-value view; interpretations remain hypotheses."""
import argparse
import csv
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("csv_path", type=Path)
args = parser.parse_args()
os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parent / ".matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

with args.csv_path.open(newline="", encoding="utf-8") as handle:
    rows = list(csv.DictReader(handle))
metadata = json.loads(args.csv_path.with_suffix(".json").read_text(encoding="utf-8"))
fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True)
groups = [("r0001",), ("r0002", "r0004"), ("r0003",), ("r0018",)]
for ax, keys in zip(axes, groups):
    for key in keys:
        values = [int(row[key]) if row[key] else None for row in rows]
        values = [float("nan") if v is None else v - 65536 if v >= 32768 else v for v in values]
        ax.plot([float(row["elapsed_s"]) for row in rows], values,
                label=key, linestyle="--" if key == "r0004" else "-")
    for cmd in metadata["commands"]:
        ax.axvline(cmd["elapsed_s"], color="gray", alpha=0.5, linewidth=0.8)
    ax.legend(loc="best")
    ax.grid(alpha=0.25)
    ax.set_ylabel("int16 interpretation")
for cmd in metadata["commands"]:
    axes[0].text(cmd["elapsed_s"], 1.02, cmd["label"], rotation=30,
                 transform=axes[0].get_xaxis_transform(), fontsize=8)
axes[-1].set_xlabel("Elapsed seconds (lines mark command responses, not physical transitions)")
fig.suptitle(f"Slave {metadata['slave']}: signed register view", y=0.995)
fig.tight_layout(rect=(0, 0, 1, 0.94))
output = args.csv_path.with_name(args.csv_path.stem + "_signed.png")
fig.savefig(output, dpi=140)
plt.close(fig)
print(output)
