#!/usr/bin/env python
"""Joint 2/3 (and later 4) control module for the 2/3 link-arm board (fw2).

Verified interface -- see JOINT3_CONTROL.md for the evidence:

  reg0x00  R   velocity (internal units, same scale as 0x0A)
  reg0x01  R   position (signed 16-bit counts)      1 count = 0.09 deg
  reg0x02  R   position-loop error / stall indicator (normal <200, blocked: thousands)
  reg0x04  R   mirror of 0x02
  reg0x05  W   DANGER: 0xAA11 = "go home to -2700".  This module NEVER writes it.
  reg0x07  R/W mode (1 = position mode)
  reg0x08  R/W enable flag.  Writing 0 removes drive (PWM off, joint goes limp).
  reg0x09  W   start command 0x1234 (unused here)
  reg0x0A  W   speed limit (internal units, max 732)
  reg0x0B  W   absolute target position -> motion
  reg0x17  R   fault (0 = ok)
  reg0x19/0x1B/0x1C  identity 2 / 0x67C6 / 0xD81D

Safety rules enforced here:
  * identity is verified before any write;
  * reg0x05 and the 0xAA/0xAA55 reset are unreachable through this API;
  * targets outside [limit_lo, limit_hi] are refused (counts, relative to the
    power-on zero);
  * "send command -> silent wait -> single read": never polls while moving,
    because polling during motion hangs the slave (needs a power cycle).

CLI (motion needs --yes; otherwise it is a dry run):
  python joint3.py status                      # read-only
  python joint3.py move --rel-deg -20 --yes
  python joint3.py move --deg 45 --yes
  python joint3.py stop                        # reg8=0, removes drive
  python joint3.py enable
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

import serial

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from safe_rs485_probe import build_read_holding, crc16_modbus, read_exact

COUNTS_PER_DEG = 100.0 / 9.0       # joint 3 measured: 1 count = 0.09 deg (joint 4 unverified)
DEG_PER_COUNT = 0.09
SPEED_MAX = 732
# per-slave firmware identity = (reg0x19, reg0x1B, reg0x1C)
IDENTITIES = {
    3: (2, 0x67C6, 0xD81D),   # 2/3 link-arm board, channel 3
    4: (2, 0x67C6, 0xD824),   # joint-4 board -- same layout as joint 3, different build
    5: (2, 0x67C6, 0xD824),   # wrist roll -- same build as joint 4; needs mode/enable setup
}
# measured degrees per count
DEG_PER_COUNT_BY_SLAVE = {
    3: 0.09,    # measured: 500 counts ~ 45 deg
    4: 0.09,    # measured: 500 counts ~ 45 deg
    5: 0.237,   # measured: pos=0 -> 0 deg (gripper level with arm), pos=676 -> ~160 deg
}
# per-slave DEFAULT soft limits (counts, relative to the power-on zero position).
# These assume the joint was powered on somewhere near the middle of its travel;
# widen only after re-measuring the travel from that power-on position.
#   joint 3: travel measured ~244 deg  -> +-1100 counts (+-99 deg)
#   joint 4: travel measured 2019 counts = 181.7 deg, limits at -933 / +1086
#            from a mid-travel power-on  -> use +-850 (+-76 deg) with margin
LIMITS = {
    3: (-1100, 1100),
    4: (-850, 850),
    5: (0, 681),      # firmware clamps reg0x0B into this window (measured)
}
# conservative fraction of the speed limit actually achieved (counts/s per unit)
RATE_FACTOR = 0.5


def build_write10(slave: int, reg: int, value: int) -> bytes:
    body = bytes([slave & 0xFF, 0x10, (reg >> 8) & 0xFF, reg & 0xFF,
                  0x00, 0x01, 0x02, (value >> 8) & 0xFF, value & 0xFF])
    crc = crc16_modbus(body)
    return body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def build_write06(slave: int, reg: int, value: int) -> bytes:
    body = bytes([slave & 0xFF, 0x06, (reg >> 8) & 0xFF, reg & 0xFF,
                  (value >> 8) & 0xFF, value & 0xFF])
    crc = crc16_modbus(body)
    return body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def _s16(v: int) -> int:
    return v - 65536 if v >= 32768 else v


def beep():
    try:
        import winsound
        winsound.Beep(1400, 350)
    except Exception:
        sys.stdout.write("\a")
        sys.stdout.flush()


class JointError(RuntimeError):
    pass


class Joint:
    """Blocking, safety-checked controller for one channel of the fw2 board."""

    def __init__(self, port: str = "COM8", slave: int = 3, speed: int = 300,
                 limit_lo: int | None = None, limit_hi: int | None = None,
                 timeout: float = 1.0, verbose: bool = True):
        if slave not in IDENTITIES:
            raise ValueError("slave must be one of %s" % sorted(IDENTITIES))
        if limit_lo is None or limit_hi is None:
            limit_lo, limit_hi = LIMITS[slave]
        self.deg_per_count = DEG_PER_COUNT_BY_SLAVE[slave]
        if not 1 <= speed <= SPEED_MAX:
            raise ValueError(f"speed must be 1..{SPEED_MAX}")
        if limit_lo >= limit_hi:
            raise ValueError("limit_lo must be < limit_hi")
        self.port, self.slave, self.speed = port, slave, speed
        self.limit_lo, self.limit_hi = limit_lo, limit_hi
        self.timeout = timeout
        self.verbose = verbose
        self.ser: Optional[serial.Serial] = None

    # ---- plumbing -------------------------------------------------------
    def __enter__(self):
        self.ser = serial.Serial(self.port, 115200, timeout=self.timeout)
        time.sleep(0.1)
        self.ser.reset_input_buffer()
        return self

    def __exit__(self, *exc):
        if self.ser:
            self.ser.close()
        self.ser = None

    def _log(self, msg: str):
        if self.verbose:
            print(msg, flush=True)

    def _wr(self, reg: int, value: int, tries: int = 6) -> bool:
        # hard block on the two dangerous writes
        if reg == 0x05:
            raise JointError("reg0x05 is blocked (0xAA11 = dangerous homing)")
        if reg == 0x00 and (value & 0xFFFF) in (0xAA11, 0xAA55):
            raise JointError("0xAA11/0xAA55 magic write is blocked")
        frame = build_write10(self.slave, reg, value & 0xFFFF)
        for _ in range(tries):
            self.ser.reset_input_buffer()
            self.ser.write(frame)
            self.ser.flush()
            resp = read_exact(self.ser, 8, self.timeout)
            if len(resp) == 8 and resp[:6] == frame[:6]:
                return True
            time.sleep(0.25)
        return False

    def _rd(self, start: int, count: int, tries: int = 4):
        need = 3 + count * 2 + 2
        for _ in range(tries):
            frame = build_read_holding(self.slave, start, count)
            self.ser.reset_input_buffer()
            self.ser.write(frame)
            self.ser.flush()
            resp = read_exact(self.ser, need, self.timeout)
            if len(resp) == need and crc16_modbus(resp[:-2]) == int.from_bytes(resp[-2:], "little"):
                return tuple(int.from_bytes(resp[3 + 2 * i: 5 + 2 * i], "big")
                             for i in range(count))
            time.sleep(0.25)
        return None

    # ---- reads ----------------------------------------------------------
    def state(self) -> dict:
        """Single read of the interesting registers."""
        w = self._rd(0x00, 0x18)
        if w is None:
            raise JointError("no response from slave %d" % self.slave)
        return {"vel": _s16(w[0x00]), "pos": _s16(w[0x01]), "err": _s16(w[0x02]),
                "mode": w[0x07], "run": w[0x08], "speed": w[0x0A],
                "target": _s16(w[0x0B]), "fault": _s16(w[0x17])}

    def verify(self) -> dict:
        w = self._rd(0x00, 0x24)
        if w is None:
            raise JointError("no response from slave %d" % self.slave)
        ident = (w[0x19], w[0x1B], w[0x1C])
        expected = IDENTITIES[self.slave]
        if ident != expected:
            raise JointError(f"firmware identity mismatch: {ident} != {expected}")
        if w[0x17]:
            raise JointError(f"fault register 0x{w[0x17]:04X}")
        if w[0x05]:
            raise JointError("reg0x05 is nonzero (unhandled command/state)")
        # slave 5 powers up with mode=0 / run=0 and writing reg0x0B does NOT enable
        # its outputs, so prepare() has to set mode + enable first.
        if self.slave in (3, 4) and w[7] != 1:
            raise JointError(f"mode reg0x07={w[7]}, expected 1 (position mode)")
        return {"pos": _s16(w[1]), "run": w[8], "target": _s16(w[0x0B])}

    def prepare(self):
        """Force position mode + speed limit + enable.  Required for slave 5."""
        if self.slave == 5 and not self._wr(0x07, 1):
            raise JointError("failed to set mode (reg0x07=1)")
        if not self._wr(0x0A, self.speed):
            raise JointError("failed to set speed limit (reg0x0A)")
        if not self._wr(0x08, 1):
            raise JointError("failed to enable (reg0x08=1)")
        time.sleep(0.3)

    # ---- writes ---------------------------------------------------------
    def set_speed(self, speed: int) -> bool:
        if not 1 <= speed <= SPEED_MAX:
            raise ValueError(f"speed must be 1..{SPEED_MAX}")
        self.speed = speed
        return self._wr(0x0A, speed)

    def stop(self) -> bool:
        """reg8=0 -> firmware clears CCER, PWM outputs off.  Joint goes limp."""
        ok = self._wr(0x08, 0)
        self._log("stop (reg8=0): %s" % ("OK" if ok else "NO ACK"))
        return ok

    def enable(self) -> bool:
        ok = self._wr(0x08, 1)
        self._log("enable (reg8=1): %s" % ("OK" if ok else "NO ACK"))
        return ok

    def _check_target(self, counts: int):
        if not -32768 <= counts <= 32767:
            raise JointError("target does not fit in int16")
        if not self.limit_lo <= counts <= self.limit_hi:
            raise JointError(
                "target %d out of soft limits [%d, %d] (%.1f deg); "
                "widen limits only after confirming travel"
                % (counts, self.limit_lo, self.limit_hi, counts * self.deg_per_count))

    def move_to(self, counts: int, settle_tol: int = 10, total_timeout: float = 30.0) -> dict:
        """Move to an absolute count.  Silent wait, then reads (never polls while moving)."""
        self._check_target(counts)
        cur = self.state()
        self.prepare()
        dist = abs(counts - cur["pos"])
        wait = dist / max(1.0, RATE_FACTOR * self.speed) + 0.9
        self._log("move: %d -> %d (%+.1f counts, %+.1f deg); est %.1fs"
                  % (cur["pos"], counts, counts - cur["pos"], (counts - cur["pos"]) * self.deg_per_count, wait))
        beep()
        if not self._wr(0x0B, counts):
            raise JointError("target write not acknowledged")
        time.sleep(wait)                      # silent: no reads while moving
        deadline = time.monotonic() + total_timeout
        st = self.state()
        while abs(st["pos"] - counts) > settle_tol and time.monotonic() < deadline:
            time.sleep(0.35)
            st = self.state()
        short = st["pos"] - counts
        st["short"] = short
        if abs(st["err"]) > 500:
            st["blocked"] = True
            self._log("WARNING: loop error reg0x02=%d -> blocked or stalled against a limit"
                      % st["err"])
        elif abs(short) > settle_tol:
            st["blocked"] = False
            self._log("WARNING: settled %d counts short of target" % short)
        else:
            st["blocked"] = False
        self._log("done: pos=%d (%.2f deg) target=%d short=%d vel=%d err=%d"
                  % (st["pos"], st["pos"] * self.deg_per_count, counts, short, st["vel"], st["err"]))
        return st

    def move_rel_deg(self, deg: float) -> dict:
        cur = self.state()
        return self.move_to(cur["pos"] + int(round(deg / self.deg_per_count)))

    def move_to_deg(self, deg: float) -> dict:
        return self.move_to(int(round(deg / self.deg_per_count)))


# ---- CLI ----------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--slave", type=int, choices=(3, 4, 5), default=3)
    ap.add_argument("--speed", type=int, default=300)
    ap.add_argument("--limit-lo", type=int, default=None)
    ap.add_argument("--limit-hi", type=int, default=None)
    ap.add_argument("--yes", action="store_true", help="actually move (otherwise dry run)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("stop")
    sub.add_parser("enable")
    m = sub.add_parser("move")
    m.add_argument("--yes", action="store_true", default=argparse.SUPPRESS,
                   help="actually move (same as the global --yes)")
    g = m.add_mutually_exclusive_group(required=True)
    g.add_argument("--deg", type=float, help="absolute angle from power-on zero")
    g.add_argument("--counts", type=int, help="absolute count target")
    g.add_argument("--rel-deg", type=float, help="relative angle")
    args = ap.parse_args(argv)

    with Joint(args.port, args.slave, args.speed, args.limit_lo, args.limit_hi) as j:
        if args.cmd == "status":
            j.verify()
            st = j.state()
            print("slave %d: pos=%d (%.2f deg) target=%d vel=%d err=%d run=%d mode=%d "
                  "speed=%d fault=%d"
                  % (args.slave, st["pos"], st["pos"] * j.deg_per_count, st["target"],
                     st["vel"], st["err"], st["run"], st["mode"], st["speed"], st["fault"]))
            return 0
        if args.cmd == "stop":
            j.verify(); j.stop(); time.sleep(0.4)
            print("after stop:", j.state()); return 0
        if args.cmd == "enable":
            j.verify(); j.enable(); time.sleep(0.3)
            print("after enable:", j.state()); return 0
        # move
        j.verify()
        cur = j.state()
        dpc = j.deg_per_count
        if args.deg is not None:
            tgt = int(round(args.deg / dpc))
        elif args.counts is not None:
            tgt = args.counts
        else:
            tgt = cur["pos"] + int(round(args.rel_deg / dpc))
        print("current pos=%d (%.2f deg); target=%d (%.2f deg); speed=%d; limits=[%d,%d]"
              % (cur["pos"], cur["pos"] * dpc, tgt, tgt * dpc,
                 args.speed, j.limit_lo, j.limit_hi))
        if not (j.limit_lo <= tgt <= j.limit_hi):
            print("REFUSED: target outside soft limits"); return 2
        if not getattr(args, "yes", False):
            print("dry run: add --yes to execute")
            return 0
        j.set_speed(args.speed)
        time.sleep(0.2)
        j.move_to(tgt)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
