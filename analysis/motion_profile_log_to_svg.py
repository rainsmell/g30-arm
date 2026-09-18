"""Create a dependency-free SVG chart from profile_motion_registers CSV output."""

from __future__ import annotations

import argparse
import csv
import html
import math
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    csv_path = Path(args.csv_path)
    out = Path(args.out) if args.out else csv_path.with_suffix(".svg")
    with csv_path.open(encoding="utf-8", newline="") as handle:
        samples = list(csv.DictReader(handle))

    regs = []
    if samples:
        for key in samples[0].keys():
            if not (key.startswith("r") and len(key) == 5):
                continue
            values = [row[key] for row in samples if row.get(key, "") != ""]
            if len(values) >= 2 and len(set(values)) > 1:
                regs.append(key)

    if not regs:
        print("No changing register series found.")
        return 0

    width, cell_w, cell_h, margin = 1400, 430, 260, 55
    columns = 3
    row_count = math.ceil(len(regs) / columns)
    height = margin + row_count * cell_h + 45
    max_time = max(float(row["elapsed_s"]) for row in samples)
    max_time = max(max_time, 1.0)

    phase_colors = {
        "contract": "#b22222",
        "expand": "#1f77b4",
        "pre": "#666666",
        "post": "#2ca02c",
    }
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="20" y="30" font-family="sans-serif" font-size="20">'
        'RS485 motion register profile</text>',
    ]

    for index, key in enumerate(regs):
        col, row_index = index % columns, index // columns
        x0 = 20 + col * cell_w
        y0 = margin + row_index * cell_h
        plot_x, plot_y = x0 + 45, y0 + 25
        plot_w, plot_h = cell_w - 70, cell_h - 65
        points = [
            (float(row["elapsed_s"]), float(row[key]))
            for row in samples
            if row.get(key, "") != ""
        ]
        ymin = min(value for _, value in points)
        ymax = max(value for _, value in points)
        if ymin == ymax:
            ymin -= 1
            ymax += 1

        title = html.escape("Register 0x" + key[1:])
        parts.append(
            f'<text x="{x0}" y="{y0 + 15}" font-family="sans-serif" '
            f'font-size="15">{title}</text>'
        )
        parts.append(
            f'<rect x="{plot_x}" y="{plot_y}" width="{plot_w}" '
            f'height="{plot_h}" fill="#fafafa" stroke="#999"/>'
        )

        seen_phases = set()
        for sample in samples:
            phase = sample.get("phase", "")
            if phase in seen_phases:
                continue
            seen_phases.add(phase)
            tx = plot_x + float(sample["elapsed_s"]) / max_time * plot_w
            parts.append(
                f'<line x1="{tx:.1f}" y1="{plot_y}" x2="{tx:.1f}" '
                f'y2="{plot_y + plot_h}" '
                f'stroke="{phase_colors.get(phase, "#999")}" '
                'stroke-opacity="0.35"/>'
            )

        polyline = []
        for timestamp, value in points:
            px = plot_x + timestamp / max_time * plot_w
            py = plot_y + (ymax - value) / (ymax - ymin) * plot_h
            polyline.append(f"{px:.1f},{py:.1f}")
        parts.append(
            f'<polyline points="{" ".join(polyline)}" fill="none" '
            'stroke="#111" stroke-width="1.5"/>'
        )
        parts.append(
            f'<text x="{plot_x}" y="{plot_y + plot_h + 20}" '
            'font-family="sans-serif" font-size="11">0s</text>'
        )
        parts.append(
            f'<text x="{plot_x + plot_w - 35}" '
            f'y="{plot_y + plot_h + 20}" font-family="sans-serif" '
            f'font-size="11">{max_time:.1f}s</text>'
        )
        parts.append(
            f'<text x="{x0}" y="{plot_y + plot_h}" '
            'font-family="sans-serif" font-size="11">'
            f'{ymin:g}..{ymax:g}</text>'
        )

    parts.append("</svg>")
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"written: {out}")
    print("changed registers:", " ".join("0x" + key[1:] for key in regs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
