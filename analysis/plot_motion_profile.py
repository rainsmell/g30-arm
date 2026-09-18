"""Plot an existing motion-profile CSV with matplotlib.

This script is read-only: it never opens the serial port and never sends
commands. It is intended for files produced by profile_motion_registers.py.
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path


def numeric(value: str):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Plot register changes from a motion-profile CSV"
    )
    ap.add_argument("csv_path")
    ap.add_argument("--out", default="")
    ap.add_argument(
        "--all",
        action="store_true",
        help="plot every register with at least two distinct valid values",
    )
    args = ap.parse_args()

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is not installed")
        return 2

    csv_path = Path(args.csv_path)
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        print("CSV is empty")
        return 1

    register_keys = [
        key for key in rows[0].keys()
        if key.startswith("r") and len(key) == 5
    ]
    changed = []
    series = {}
    for key in register_keys:
        points = []
        for row in rows:
            value = numeric(row.get(key, ""))
            timestamp = numeric(row.get("elapsed_s", ""))
            if value is not None and timestamp is not None:
                points.append((timestamp, value))
        if len(points) >= 2 and len({value for _, value in points}) > 1:
            changed.append(key)
            series[key] = points

    if not changed:
        print("No changing register series found")
        return 0

    preferred = ["r0001", "r0002", "r000B", "r0018", "r001F"]
    if not args.all:
        selected = [key for key in preferred if key in changed]
        if selected:
            changed = selected

    output = Path(args.out) if args.out else csv_path.with_name(
        csv_path.stem + "_matplotlib.png"
    )
    columns = 2
    plot_rows = math.ceil(len(changed) / columns)
    fig, axes = plt.subplots(
        plot_rows,
        columns,
        figsize=(14, max(4.2 * plot_rows, 5.0)),
        squeeze=False,
    )

    phase_colors = {
        "pre": "#777777",
        "contract": "#f2b6b6",
        "expand": "#b9d7f0",
        "post": "#b9e2b9",
    }
    first_phase = {}
    for row in rows:
        phase = row.get("phase", "")
        timestamp = numeric(row.get("elapsed_s", ""))
        if phase and timestamp is not None and phase not in first_phase:
            first_phase[phase] = timestamp

    max_time = max(
        numeric(row.get("elapsed_s", "")) or 0.0 for row in rows
    )
    phase_order = sorted(first_phase.items(), key=lambda item: item[1])
    for index, key in enumerate(changed):
        ax = axes[index // columns][index % columns]
        points = series[key]
        x = [point[0] for point in points]
        y = [point[1] for point in points]
        ax.plot(x, y, "o-", linewidth=1.1, markersize=3, color="#222222")
        ax.set_title("Register 0x" + key[1:])
        ax.set_xlabel("elapsed time (s)")
        ax.set_ylabel("raw uint16 value")
        ax.grid(True, alpha=0.3)

        for phase_index, (phase, start) in enumerate(phase_order):
            end = (
                phase_order[phase_index + 1][1]
                if phase_index + 1 < len(phase_order)
                else max_time
            )
            ax.axvspan(
                start,
                end,
                color=phase_colors.get(phase, "#eeeeee"),
                alpha=0.22,
                linewidth=0,
                label=phase,
            )
        # Re-draw the data over the shaded phase bands.
        ax.plot(x, y, "o-", linewidth=1.1, markersize=3, color="#222222")
        ax.legend(loc="best", fontsize=8)

    for index in range(len(changed), plot_rows * columns):
        axes[index // columns][index % columns].axis("off")

    fig.suptitle(
        f"Slave motion register profile: {csv_path.name}\n"
        "phase shading: contraction / expansion"
    )
    fig.tight_layout()
    fig.savefig(output, dpi=160)
    plt.close(fig)

    print("written:", output)
    print("plotted:", " ".join("0x" + key[1:] for key in changed))
    print("sample rows:", len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
