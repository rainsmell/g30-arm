r"""
Run the established contraction/expansion sequence for one joint while
sampling readable registers at a fixed rate.

Safety:
  - This script sends motion commands only when --run is supplied.
  - It always attempts to restore reg 0x001F=1 in a finally block.
    An acknowledgement or register readback does not prove physical stop.
  - It addresses exactly one slave per run.
  - It never writes the maintenance/reset commands.

Example:
    python analysis\profile_motion_registers.py --slave 3 --run --beep --plot

The CSV and JSON files are written below analysis/motion_logs by default.
Plotting is optional; it requires matplotlib.
Plots interpret registers 0x0001..0x0004 as int16; CSV retains raw uint16.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

try:
    import serial
except ImportError:
    serial = None

try:
    # When launched as "python analysis\profile_motion_registers.py", the
    # analysis directory is on sys.path.
    from safe_rs485_probe import crc16_modbus, probe_read, read_exact
except ImportError as exc:
    print(f"cannot import safe_rs485_probe: {exc}", file=sys.stderr)
    raise


# One bulk read covers every register used in the previous snapshots. The
# previous captures used 0x0001/0x25; include 0x0000 to test a 32-bit pair.
# Abort before motion if the extended range cannot be read.
READ_GROUPS: Tuple[Tuple[int, int], ...] = (
    (0x0000, 0x26),  # 0x0000..0x0025 inclusive
)

REGS: List[int] = []
for _start, _count in READ_GROUPS:
    REGS.extend(range(_start, _start + _count))

# Working interpretation for slaves 3/4, not a confirmed firmware type map.
SIGNED_PLOT_REGS = frozenset((0x0001, 0x0002, 0x0003, 0x0004))
DERIVED_FIELDS = ("u32_be_r0000_r0001", "u32_le_r0000_r0001")


def plot_value(reg: int, value: int) -> int:
    return value - 0x10000 if reg in SIGNED_PLOT_REGS and value >= 0x8000 else value


def parse_int(value: str) -> int:
    return int(value, 0)


def build_write10(slave: int, reg: int, value: int) -> bytes:
    body = bytes(
        [
            slave & 0xFF,
            0x10,
            (reg >> 8) & 0xFF,
            reg & 0xFF,
            0x00,
            0x01,
            0x02,
            (value >> 8) & 0xFF,
            value & 0xFF,
        ]
    )
    crc = crc16_modbus(body)
    return body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def send_write10(
    ser,
    slave: int,
    reg: int,
    value: int,
    timeout_s: float,
) -> Tuple[bytes, Optional[str]]:
    """Send one previously validated function-0x10 single-word frame."""
    frame = build_write10(slave, reg, value)
    ser.reset_input_buffer()
    ser.write(frame)
    ser.flush()
    response = read_exact(ser, 8, timeout_s)

    if len(response) != 8:
        return response, f"short write response ({len(response)}/8 bytes)"
    if response[:6] != bytes(
        [slave & 0xFF, 0x10, (reg >> 8) & 0xFF, reg & 0xFF, 0x00, 0x01]
    ):
        return response, "unexpected write response header"
    rx_crc = response[-2] | (response[-1] << 8)
    calc_crc = crc16_modbus(response[:-2])
    if rx_crc != calc_crc:
        return response, (
            f"write response CRC mismatch rx=0x{rx_crc:04X} "
            f"calc=0x{calc_crc:04X}"
        )
    return response, None


def read_register_groups(
    ser,
    slave: int,
    timeout_s: float,
) -> Tuple[Dict[int, Optional[int]], List[str]]:
    values: Dict[int, Optional[int]] = {reg: None for reg in REGS}
    errors: List[str] = []

    for start, count in READ_GROUPS:
        response, error = probe_read(
            ser, slave, start, count, timeout_s, quiet=True
        )
        if error:
            errors.append(f"0x{start:04X}/{count}: {error}")
            continue

        data = response[3:-2]
        if len(data) != count * 2:
            errors.append(
                f"0x{start:04X}/{count}: data length {len(data)} "
                f"expected {count * 2}"
            )
            continue

        for index in range(count):
            offset = index * 2
            values[start + index] = (data[offset] << 8) | data[offset + 1]

    return values, errors


def beep() -> None:
    """Give an audible warning immediately before the first motion write."""
    try:
        import winsound

        winsound.Beep(1200, 220)
    except Exception:
        # Non-Windows or unavailable audio: terminal bell is harmless.
        print("\a", end="", flush=True)


def make_tag(slave: int) -> str:
    return f"slave{slave}_motion_profile_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def append_sample(
    samples: List[dict],
    ser,
    slave: int,
    timeout_s: float,
    phase: str,
    t0: float,
    sample_index: int,
) -> Tuple[int, List[str]]:
    values, errors = read_register_groups(ser, slave, timeout_s)
    row = {
        "sample_index": sample_index,
        "elapsed_s": round(time.monotonic() - t0, 6),
        "wall_time": datetime.now().isoformat(timespec="milliseconds"),
        "phase": phase,
    }
    for reg in REGS:
        row[f"r{reg:04X}"] = values.get(reg)
    high, low = values.get(0), values.get(1)
    row["u32_be_r0000_r0001"] = ((high << 16) | low) if high is not None and low is not None else None
    row["u32_le_r0000_r0001"] = ((low << 16) | high) if high is not None and low is not None else None
    row["read_errors"] = " | ".join(errors)
    samples.append(row)
    return sample_index + 1, errors


def monitor_phase(
    samples: List[dict],
    events: List[dict],
    ser,
    slave: int,
    timeout_s: float,
    phase: str,
    duration_s: float,
    sample_hz: float,
    t0: float,
    sample_index: int,
) -> int:
    """Sample for a phase at a fixed rate; no single long phase sleep."""
    interval = 1.0 / sample_hz
    phase_start = time.monotonic()
    print(f"[{phase}] sampling for {duration_s:g}s", flush=True)
    deadline = phase_start + duration_s
    next_sample = phase_start
    events.append(
        {
            "elapsed_s": round(phase_start - t0, 6),
            "event": f"{phase}_start",
            "phase": phase,
        }
    )

    while True:
        now = time.monotonic()
        if now >= deadline:
            break

        if now < next_sample:
            time.sleep(min(next_sample, deadline) - now)

        if time.monotonic() >= deadline:
            break

        sample_index, errors = append_sample(
            samples,
            ser,
            slave,
            timeout_s,
            phase,
            t0,
            sample_index,
        )
        if errors:
            print(
                f"[{phase}] sample {sample_index - 1}: "
                + "; ".join(errors),
                file=sys.stderr,
            )

        next_sample += interval
        # If serial reads took longer than one interval, do not try to replay
        # missed samples; resume from the current time at the requested rate.
        if next_sample < time.monotonic():
            next_sample = time.monotonic()

    events.append(
        {
            "elapsed_s": round(time.monotonic() - t0, 6),
            "event": f"{phase}_end",
            "phase": phase,
        }
    )
    print(f"[{phase}] sampling window ended (not a motion-complete signal)", flush=True)
    return sample_index


def write_outputs(
    output_dir: Path,
    tag: str,
    metadata: dict,
    samples: List[dict],
    events: List[dict],
) -> Tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"{tag}.csv"
    json_path = output_dir / f"{tag}.json"
    plot_path = output_dir / f"{tag}.png"

    fieldnames = [
        "sample_index",
        "elapsed_s",
        "wall_time",
        "phase",
        *(f"r{reg:04X}" for reg in REGS),
        "read_errors",
        *DERIVED_FIELDS,
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(samples)

    metadata = dict(metadata)
    metadata["events"] = events
    metadata["sample_count"] = len(samples)
    metadata["registers"] = [f"0x{reg:04X}" for reg in REGS]
    metadata["csv_register_encoding"] = "uint16"
    metadata["plot_signed_int16_registers"] = [
        f"0x{reg:04X}" for reg in sorted(SIGNED_PLOT_REGS)
    ]
    metadata["derived_32bit_candidates"] = {
        "u32_be_r0000_r0001": "(r0000 << 16) | r0001",
        "u32_le_r0000_r0001": "(r0001 << 16) | r0000",
    }
    json_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return csv_path, json_path, plot_path


def plot_changes(
    plot_path: Path,
    samples: List[dict],
    events: List[dict],
) -> List[int]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print(
            "matplotlib is not installed; CSV/JSON were saved, "
            "but no plot was generated. Install with: "
            "python -m pip install matplotlib",
            file=sys.stderr,
        )
        return []

    changed: List[int] = []
    for reg in REGS:
        key = f"r{reg:04X}"
        series = [row.get(key) for row in samples if row.get(key) is not None]
        if len(series) >= 2 and len(set(series)) > 1:
            changed.append(reg)

    if not changed:
        print("No register changes detected in valid samples; no plot generated.")
        return []

    columns = 3
    rows = math.ceil(len(changed) / columns)
    fig, axes = plt.subplots(
        rows,
        columns,
        figsize=(15, max(3.2 * rows, 4.0)),
        squeeze=False,
    )
    phase_colors = {
        "pre": "#777777",
        "contract": "#b22222",
        "expand": "#1f77b4",
        "post": "#2ca02c",
    }

    for index, reg in enumerate(changed):
        ax = axes[index // columns][index % columns]
        key = f"r{reg:04X}"
        x = [
            row["elapsed_s"]
            for row in samples
            if row.get(key) is not None
        ]
        y = [
            plot_value(reg, row[key])
            for row in samples
            if row.get(key) is not None
        ]
        ax.plot(x, y, marker=".", linewidth=1.0, markersize=3)
        ax.set_title(f"Register 0x{reg:04X}")
        ax.set_ylabel("int16 (assumed)" if reg in SIGNED_PLOT_REGS else "uint16")
        ax.set_xlabel("elapsed (s)")
        ax.grid(True, alpha=0.3)

        for event in events:
            if event["event"].endswith("_start"):
                phase = event["phase"]
                ax.axvline(
                    event["elapsed_s"],
                    color=phase_colors.get(phase, "black"),
                    alpha=0.35,
                    linewidth=1,
                )

    for index in range(len(changed), rows * columns):
        axes[index // columns][index % columns].axis("off")

    fig.suptitle("RS485 motion register profile")
    fig.tight_layout()
    fig.savefig(plot_path, dpi=140)
    plt.close(fig)
    if any(row.get("r0000") is not None for row in samples):
        fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
        times = [row["elapsed_s"] for row in samples]
        axes[0].plot(times, [row.get("r0000") if row.get("r0000") is not None else math.nan for row in samples])
        axes[0].set_ylabel("r0000 (uint16)")
        for ax, key in zip(axes[1:], DERIVED_FIELDS):
            unsigned = [row.get(key) for row in samples]
            ax.plot(times, [v if v is not None else math.nan for v in unsigned], label="uint32")
            ax.plot(times, [math.nan if v is None else v - 0x100000000 if v >= 0x80000000 else v for v in unsigned], label="int32", linestyle="--")
            ax.set_ylabel(key)
            ax.legend()
        for ax in axes:
            ax.grid(alpha=0.3)
        axes[-1].set_xlabel("elapsed (s)")
        fig.suptitle("Candidate 32-bit interpretations (not confirmed)")
        fig.tight_layout()
        candidate_path = plot_path.with_name(plot_path.stem + "_32bit.png")
        fig.savefig(candidate_path, dpi=140)
        plt.close(fig)
        print("32-bit plot:", candidate_path)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run one-joint contraction/expansion while recording readable "
            "registers at a fixed rate."
        )
    )
    parser.add_argument("--port", default="COM8")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--slave",
        type=parse_int,
        choices=(3, 4, 5),
        default=3,
        help="joint slave id; 5 is the wrist-roll joint",
    )
    parser.add_argument("--speed", type=parse_int, default=0x1482)
    parser.add_argument("--contract-seconds", type=float, default=10.0)
    parser.add_argument("--expand-seconds", type=float, default=15.0)
    parser.add_argument("--post-seconds", type=float, default=3.0,
                        help="read-only sampling duration after final inhibit")
    parser.add_argument("--sample-hz", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=0.5)
    parser.add_argument("--settle-ms", type=int, default=120)
    parser.add_argument("--output-dir", default="analysis/motion_logs")
    parser.add_argument("--tag", default="")
    parser.add_argument("--beep", action="store_true")
    parser.add_argument("--plot", action="store_true")
    parser.add_argument(
        "--run",
        action="store_true",
        help="actually send motion commands; without this the script is dry-run",
    )
    args = parser.parse_args()

    if not math.isfinite(args.sample_hz) or args.sample_hz <= 0:
        parser.error("--sample-hz must be positive")
    if any(not math.isfinite(value) or value <= 0 for value in
           (args.contract_seconds, args.expand_seconds, args.post_seconds, args.timeout)):
        parser.error("phase durations and timeout must be finite and positive")
    if serial is None:
        print("pyserial is not installed", file=sys.stderr)
        return 2

    slave = args.slave
    speed = args.speed & 0xFFFF
    tag = args.tag or make_tag(slave)
    output_dir = Path(args.output_dir)

    sequence = [
        f"{slave:02X} 10 00 1F 00 01 02 {speed >> 8:02X} "
        f"{speed & 0xFF:02X}",
        f"{slave:02X} 10 00 0B 00 01 02 AA 11",
        f"{slave:02X} 10 00 0B 00 01 02 00 00",
        f"{slave:02X} 10 00 1F 00 01 02 00 01",
    ]
    print("Target:", f"slave {slave} on {args.port} at {args.baud} 8N1")
    print("Sequence (CRC is generated by the script):")
    print("  speed:", sequence[0])
    print("  contract:", sequence[1], f"for {args.contract_seconds:g}s")
    print("  expand:", sequence[2], f"for {args.expand_seconds:g}s")
    print("  final inhibit:", sequence[3])
    print("Sample rate:", f"{args.sample_hz:g} Hz")
    print("Post-inhibit sampling:", f"{args.post_seconds:g}s")
    print("Fixed-duration test; physical motion completion is not detected.")
    print("Output tag:", tag)

    if not args.run:
        print("Dry-run only. Add --run to send commands.")
        return 0

    samples: List[dict] = []
    events: List[dict] = []
    command_log: List[dict] = []
    all_errors: List[str] = []
    sample_index = 0
    t0 = time.monotonic()
    inhibit_attempted = False

    metadata = {
        "created_local": datetime.now().isoformat(timespec="seconds"),
        "port": args.port,
        "baud": args.baud,
        "slave": slave,
        "speed_override": f"0x{speed:04X}",
        "contract_seconds": args.contract_seconds,
        "expand_seconds": args.expand_seconds,
        "post_seconds": args.post_seconds,
        "physical_stop_confirmed": False,
        "sample_hz": args.sample_hz,
        "read_only_register_sampling": True,
        "motion_commands_sent": True,
        "sequence_note": (
            "The two motion phases are sampled at fixed rate; there is no "
            "single sleep for either phase."
        ),
    }

    try:
        with serial.Serial(
            args.port,
            args.baud,
            bytesize=8,
            parity=serial.PARITY_NONE,
            stopbits=1,
            timeout=args.timeout,
        ) as ser:
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            sample_index, errors = append_sample(
                samples, ser, slave, args.timeout, "pre", t0, sample_index
            )
            all_errors.extend(errors)

            if errors:
                raise RuntimeError("Preflight extended register read failed; motion sequence aborted")

            if args.beep:
                beep()
            time.sleep(0.05)

            def command(reg: int, value: int, label: str) -> None:
                nonlocal all_errors
                frame = build_write10(slave, reg, value)
                response, error = send_write10(
                    ser, slave, reg, value, args.timeout
                )
                entry = {
                    "elapsed_s": round(time.monotonic() - t0, 6),
                    "label": label,
                    "tx": frame.hex(" ").upper(),
                    "rx": response.hex(" ").upper(),
                    "error": error,
                }
                command_log.append(entry)
                print(
                    f"{label}: TX {entry['tx']} "
                    f"RX {entry['rx'] or '<none>'}"
                )
                if error:
                    all_errors.append(f"{label}: {error}")
                    raise RuntimeError(f"{label}: {error}")

            command(0x001F, speed, "SPEED")
            time.sleep(max(args.settle_ms, 0) / 1000.0)

            command(0x000B, 0xAA11, "CONTRACT_START")
            sample_index = monitor_phase(
                samples,
                events,
                ser,
                slave,
                args.timeout,
                "contract",
                args.contract_seconds,
                args.sample_hz,
                t0,
                sample_index,
            )

            command(0x000B, 0x0000, "EXPAND_START")
            sample_index = monitor_phase(
                samples,
                events,
                ser,
                slave,
                args.timeout,
                "expand",
                args.expand_seconds,
                args.sample_hz,
                t0,
                sample_index,
            )

            sample_index, errors = append_sample(
                samples, ser, slave, args.timeout, "pre_inhibit", t0, sample_index
            )
            all_errors.extend(errors)
    except KeyboardInterrupt:
        print("Interrupted; attempting final PWM inhibit.", file=sys.stderr)
        all_errors.append("KeyboardInterrupt")
    except Exception as exc:
        print(f"test failed: {exc}", file=sys.stderr)
        all_errors.append(str(exc))
    finally:
        # Re-open the port if necessary only when the serial object was closed
        # by an exception inside the with block.  This is intentionally a
        # simple best-effort safety action.
        try:
            with serial.Serial(
                args.port,
                args.baud,
                bytesize=8,
                parity=serial.PARITY_NONE,
                stopbits=1,
                timeout=args.timeout,
            ) as safe_ser:
                inhibit_attempted = True
                frame = build_write10(slave, 0x001F, 0x0001)
                response, error = send_write10(
                    safe_ser, slave, 0x001F, 0x0001, args.timeout
                )
                entry = {
                    "elapsed_s": round(time.monotonic() - t0, 6),
                    "label": "FINAL_INHIBIT",
                    "tx": frame.hex(" ").upper(),
                    "rx": response.hex(" ").upper(),
                    "error": error,
                }
                command_log.append(entry)
                print(
                    f"FINAL_INHIBIT: TX {entry['tx']} "
                    f"RX {entry['rx'] or '<none>'}"
                )
                if error:
                    all_errors.append(f"FINAL_INHIBIT: {error}")
                metadata["final_inhibit_acknowledged"] = error is None
                events.append({
                    "elapsed_s": entry["elapsed_s"],
                    "event": "final_inhibit_response",
                    "phase": "post",
                })
                print(f"Inhibit acknowledgement: {error is None}; physical stop is UNCONFIRMED.", flush=True)
                sample_index = monitor_phase(
                    samples, events, safe_ser, slave, args.timeout, "post",
                    args.post_seconds, args.sample_hz, t0, sample_index,
                )
                post_values = [row["r001F"] for row in samples
                               if row["phase"] == "post" and row["r001F"] is not None]
                metadata["final_inhibit_last_readback"] = post_values[-1] if post_values else None
                if not post_values or post_values[-1] != 1:
                    all_errors.append("FINAL_INHIBIT readback missing or not 0x0001")
        except Exception as exc:
            all_errors.append(f"FINAL_INHIBIT failed: {exc}")
            print(f"FINAL_INHIBIT failed: {exc}", file=sys.stderr)

    for row in samples:
        if row["read_errors"]:
            error = f"sample {row['sample_index']} ({row['phase']}): {row['read_errors']}"
            if error not in all_errors:
                all_errors.append(error)
    metadata.setdefault("final_inhibit_acknowledged", False)
    metadata["commands"] = command_log
    metadata["errors"] = all_errors
    metadata["final_inhibit_attempted"] = inhibit_attempted
    csv_path, json_path, plot_path = write_outputs(
        output_dir, tag, metadata, samples, events
    )
    print("CSV:", csv_path)
    print("JSON:", json_path)
    print("Recording finished. Physical stop remains UNCONFIRMED; check the joint.", flush=True)

    if args.plot:
        changed = plot_changes(plot_path, samples, events)
        if changed:
            print(
                "Changed registers:",
                " ".join(f"0x{reg:04X}" for reg in changed),
            )
            print("Plot:", plot_path)

    return 0 if not all_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
