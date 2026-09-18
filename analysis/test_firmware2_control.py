"""Bounded single-joint trial based on gd32_firmware_2.bin; dry-run by default.

Joint 2: --slave 2 --direction 0 (or 1).
Joint 3: --slave 3 --delta -100 --speed 366, position mode only.
An output-disable acknowledgement is not proof of physical stopping.
"""
import argparse
import csv
import json
import math
import os
import struct
import time
from datetime import datetime
from pathlib import Path

import serial
from safe_rs485_probe import build_read_holding, crc16_modbus
from profile_motion_registers import beep, build_write10

REG_COUNT = 0x24


def signed(value):
    return value - 65536 if value >= 32768 else value


def check_preflight(slave, words):
    # Defaults/identifiers from 0x0800B42C, C51C and BB48. Fail closed on a
    # different build instead of applying the newly recovered map blindly.
    if (words[0x19], words[0x1B], words[0x1C]) != (2, 0x67C6, 0xD81D):
        raise RuntimeError("Firmware identity differs from analyzed image; no trial started")
    if words[0x17]:
        raise RuntimeError(f"Fault register is 0x{words[0x17]:04X}; no automatic fault clear")
    if words[5]:
        raise RuntimeError("reg5 is nonzero (special command/state); power-cycle before this trial")
    if slave == 3 and words[7] != 1:
        raise RuntimeError("Slave 3 must already be in position mode (reg7=1); no automatic mode switch")
    if slave == 3 and abs(signed(words[0])) > 10:
        raise RuntimeError("Slave 3 reports motion; no new motion trial started")


class Trial:
    def __init__(self, args, ser):
        self.args, self.ser = args, ser
        self.t0 = time.monotonic()
        self.samples, self.commands, self.errors = [], [], []
        self.armed = False
        self.disable_acknowledged = False
        self.disable_readback = None
        self.target = None

    def receive(self, count, deadline):
        data = bytearray()
        while len(data) < count and time.monotonic() < deadline:
            self.ser.timeout = min(0.02, max(0.001, deadline - time.monotonic()))
            part = self.ser.read(count - len(data))
            if part:
                data.extend(part)
        return bytes(data)

    def exchange(self, frame, timeout):
        self.ser.reset_input_buffer()
        deadline = time.monotonic() + timeout
        self.ser.write_timeout = min(0.1, timeout)
        if self.ser.write(frame) != len(frame):
            raise RuntimeError("Short serial write")
        # No unbounded flush: the request is sent by the serial driver while
        # we wait for its response. All reads share one absolute deadline.
        header = self.receive(3, deadline)
        if len(header) != 3:
            raise RuntimeError("Response header timeout: " + header.hex(" "))
        length = 5 if header[1] & 0x80 else (header[2] + 5 if header[1] == 3 else 8)
        response = header + self.receive(length - 3, deadline)
        if len(response) != length:
            raise RuntimeError("Incomplete response: " + response.hex(" "))
        if crc16_modbus(response[:-2]) != int.from_bytes(response[-2:], "little"):
            raise RuntimeError("Response CRC mismatch")
        if response[0] != self.args.slave:
            raise RuntimeError("Response slave mismatch")
        if response[1] & 0x80:
            raise RuntimeError(f"Modbus exception 0x{response[2]:02X}")
        return response

    def write(self, reg, value, label):
        frame = build_write10(self.args.slave, reg, value)
        entry = {"label": label, "reg": reg, "value": value,
                 "elapsed_tx_begin": time.monotonic() - self.t0,
                 "tx": frame.hex(" ").upper(), "error": None}
        self.commands.append(entry)
        print(f"{label}: TX {entry['tx']}", flush=True)
        try:
            response = self.exchange(frame, 0.2)
            entry["rx"] = response.hex(" ").upper()
            if response[:6] != frame[:6]:
                raise RuntimeError("Write acknowledgement header mismatch")
        except Exception as exc:
            entry["error"] = str(exc)
            raise
        finally:
            entry["elapsed_end"] = time.monotonic() - self.t0

    def sample(self, phase, timeout=0.15):
        row = {"elapsed_s": time.monotonic() - self.t0, "phase": phase,
               "wall_time": datetime.now().isoformat(timespec="milliseconds"),
               **{f"r{i:04X}": None for i in range(REG_COUNT)}, "error": ""}
        try:
            response = self.exchange(build_read_holding(self.args.slave, 0, REG_COUNT), timeout)
            row["rx"] = response.hex(" ").upper()
            # Modbus response length is 3-byte header + data + 2-byte CRC.
            # For 36 words: 3 + 72 + 2 = 77 bytes.
            if response[:3] != bytes((self.args.slave, 3, REG_COUNT * 2)):
                raise RuntimeError("Read function/length mismatch")
            data = response[3:-2]
            if len(data) != REG_COUNT * 2:
                raise RuntimeError(f"Read data length {len(data)} != {REG_COUNT * 2}")
            words = struct.unpack(f">{REG_COUNT}H", data)
            row.update({f"r{i:04X}": value for i, value in enumerate(words)})
            return words
        except Exception as exc:
            row["error"] = str(exc)
            raise
        finally:
            row["elapsed_s"] = time.monotonic() - self.t0
            self.samples.append(row)

    def monitor(self, seconds, phase, deadline=None, initial_limit=None):
        deadline = deadline if deadline is not None else time.monotonic() + seconds
        next_sample = time.monotonic()
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining < 0.04:
                time.sleep(max(0, remaining))
                break
            if time.monotonic() < next_sample:
                time.sleep(min(next_sample - time.monotonic(), remaining))
                continue
            words = self.sample(phase, min(0.15, remaining))
            if words[0x17]:
                raise RuntimeError(f"{phase}: fault 0x{words[0x17]:04X}")
            if phase == "motion" and initial_limit == 0 and words[0x18] != 0:
                raise RuntimeError("Limit signal rose; ending the motion window early")
            next_sample = time.monotonic() + 1 / self.args.sample_hz

    def disable(self):
        # A second attempt is only an output-disable retry, never a restart.
        for attempt in range(2):
            try:
                self.write(8, 0, f"FINAL_DISABLE_{attempt + 1}")
                self.disable_acknowledged = True
                time.sleep(0.12)
                words = self.sample("disable_check")
                self.disable_readback = words[8]
                if words[8] == 0:
                    return
                raise RuntimeError("reg8 was re-enabled after disable")
            except (Exception, KeyboardInterrupt) as exc:
                self.errors.append(f"Disable attempt {attempt + 1}: {type(exc).__name__}: {exc}")
        raise RuntimeError("Output disable not verified; cut power if needed")

    def run(self):
        words = self.sample("pre")
        check_preflight(self.args.slave, words)
        # From here on every exit attempts disable, including failed ACKs.
        self.armed = True
        try:
            beep()
            self.write(8, 0, "PREPARE_DISABLE")
            time.sleep(0.12)
            words = self.sample("prepared")
            check_preflight(self.args.slave, words)
            if words[8] != 0:
                raise RuntimeError("Preparation did not disable outputs")
            if self.args.slave == 3:
                current = signed(words[1])
                self.target = current + self.args.delta
                if not -32768 <= self.target <= 32767:
                    raise RuntimeError("Relative target would wrap the exposed position word")
                self.write(0xA, self.args.speed, "POSITION_SPEED_LIMIT")
                time.sleep(0.12)
                checked = self.sample("configured")
                check_preflight(3, checked)
                if checked[8] != 0 or checked[0xA] != self.args.speed:
                    raise RuntimeError("Speed configuration readback differs or outputs enabled")
                print(f"Position {current}; relative {self.args.delta}; target {self.target}", flush=True)
                value = self.target & 0xFFFF
            else:
                value = self.args.direction
            deadline = time.monotonic() + self.args.seconds
            self.write(0xB, value, "START_SINGLE_TARGET" if self.args.slave == 3 else "START_DIRECTION")
            # Leave a short turnaround interval; deadline remains anchored to TX.
            time.sleep(min(0.12, max(0, deadline - time.monotonic())))
            self.monitor(self.args.seconds, "motion", deadline, words[0x18])
        finally:
            self.disable()
        self.monitor(1.0, "post")


def save_and_plot(args, trial):
    folder = Path(__file__).resolve().parent / "motion_logs"
    folder.mkdir(exist_ok=True)
    tag = f"slave{args.slave}_fw2_trial_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    stem = folder / tag
    record = {"settings": vars(args), "commands": trial.commands, "samples": trial.samples,
              "errors": trial.errors, "resolved_target": trial.target,
              "disable_acknowledged": trial.disable_acknowledged,
              "disable_readback": trial.disable_readback, "physical_stop_confirmed": False}
    stem.with_suffix(".json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    fields = ["elapsed_s", "wall_time", "phase", *[f"r{i:04X}" for i in range(REG_COUNT)], "error"]
    with stem.with_suffix(".csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(trial.samples)
    print("JSON:", stem.with_suffix(".json"))
    print("CSV:", stem.with_suffix(".csv"))
    if not args.plot or not trial.samples:
        return
    try:
        os.environ.setdefault("MPLCONFIGDIR", str(folder.parent / ".matplotlib"))
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        regs = [0, 1, 2, 8, 0xA, 0xB] if args.slave == 3 else [1, 2, 8, 0xB, 0x17, 0x18]
        fig, axes = plt.subplots(3, 2, figsize=(12, 9))
        for ax, reg in zip(axes.flat, regs):
            is_signed = args.slave == 3 and reg in (0, 1, 2, 0xA, 0xB)
            values = [r[f"r{reg:04X}"] for r in trial.samples]
            values = [math.nan if v is None else signed(v) if is_signed else v for v in values]
            ax.plot([r["elapsed_s"] for r in trial.samples], values, marker=".")
            ax.set_title(f"Register 0x{reg:04X} ({'int16' if is_signed else 'uint16'})")
            for event in trial.commands:
                ax.axvline(event["elapsed_tx_begin"], color="gray", alpha=0.3)
            ax.set_xlabel("elapsed seconds")
            ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(stem.with_suffix(".png"), dpi=140)
        plt.close(fig)
        print("Plot:", stem.with_suffix(".png"))
    except Exception as exc:
        print("Plot failed; raw logs saved:", exc)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM8")
    parser.add_argument("--slave", type=int, choices=(2, 3), required=True)
    parser.add_argument("--direction", type=int, choices=(0, 1), default=None)
    parser.add_argument("--delta", type=int, default=None)
    parser.add_argument("--speed", type=int, default=None)
    parser.add_argument("--seconds", type=float, default=2.0)
    parser.add_argument("--sample-hz", type=float, default=10.0)
    parser.add_argument("--plot", action="store_true")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    if not math.isfinite(args.seconds) or not 0 < args.seconds <= 2:
        parser.error("--seconds must be in (0,2]")
    if not math.isfinite(args.sample_hz) or not 1 <= args.sample_hz <= 10:
        parser.error("--sample-hz must be in [1,10]")
    if args.slave == 2:
        if args.delta is not None or args.speed is not None:
            parser.error("Slave 2 does not use slave 3 position/speed parameters")
        args.direction = 0 if args.direction is None else args.direction
    else:
        if args.direction is not None:
            parser.error("Slave 3 uses --delta, not --direction")
        args.delta = -100 if args.delta is None else args.delta
        args.speed = 366 if args.speed is None else args.speed
        if not 1 <= abs(args.delta) <= 300 or not 1 <= args.speed <= 732:
            parser.error("Slave 3 requires 1<=abs(delta)<=300 and 1<=speed<=732")
    print(f"Slave {args.slave} on {args.port}, 115200 8N1; motion window <= {args.seconds}s")
    print("Output disable removes drive/holding torque; support gravity-loaded parts.")
    print("Physical stop must be observed; cut power if motion continues.")
    print("PREPARE/FINAL reg8=0:", build_write10(args.slave, 8, 0).hex(" ").upper())
    if args.slave == 2:
        print("DIRECTION:", build_write10(2, 0xB, args.direction).hex(" ").upper())
    else:
        print("SPEED LIMIT:", build_write10(3, 0xA, args.speed).hex(" ").upper())
        print(f"TARGET: current signed position + ({args.delta}), computed only after preflight")
    if not args.run:
        print("Dry-run: no serial port opened. Add --run to execute one trial.")
        return 0
    trial = None
    try:
        with serial.Serial(args.port, 115200, timeout=0.02, write_timeout=0.1) as ser:
            trial = Trial(args, ser)
            trial.run()
    except (Exception, KeyboardInterrupt) as exc:
        message = f"{type(exc).__name__}: {exc}"
        print("Trial ended:", message, flush=True)
        if trial is None:
            return 1
        trial.errors.append(message)
    finally:
        if trial is not None:
            save_and_plot(args, trial)
            print(f"Disable ACK={trial.disable_acknowledged}; reg8={trial.disable_readback}; physical stop UNCONFIRMED")
    return int(bool(trial.errors))


if __name__ == "__main__":
    raise SystemExit(main())
