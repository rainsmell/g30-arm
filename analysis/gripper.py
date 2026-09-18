#!/usr/bin/env python
"""Joint 6 (gripper) open/close control for the 5/6 dual board (fw2 build 0xD824).

The gripper is a two-state plier-like device (no continuous position sensor).

Verified interface (2026-09-12):

  reg0x01  R   state code:  0 = CLOSED,  45 = moving,  90 = OPEN (180 deg)
  reg0x02  R   loop error (small; grows if blocked)
  reg0x07  R/W mode -- powers up 0, must be set to 1
  reg0x08  R/W enable -- powers up 0, must be set to 1
  reg0x0A  W   speed limit
  reg0x0B  W   target: 90 (or more) -> OPEN,  0 -> CLOSE
  reg0x17  R   fault

Sequence required (the module does it automatically):
  reg0x07 = 1  ->  reg0x0A = speed  ->  reg0x08 = 1  ->  reg0x0B = target

Timing measured: CLOSE takes ~1 s, OPEN takes ~4 s at speed 100.

CLI:
  python gripper.py status
  python gripper.py open  --yes
  python gripper.py close --yes
  python gripper.py stop
"""
from __future__ import annotations

import argparse
import sys
import time

import serial

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from safe_rs485_probe import build_read_holding, crc16_modbus, read_exact

SLAVE = 6
IDENTITY = (2, 0x67C6, 0xD824)
CLOSED, MOVING, OPEN = 0, 45, 90
TARGET_OPEN, TARGET_CLOSE = 90, 0
DEFAULT_SPEED = 100


def build_write10(slave: int, reg: int, value: int) -> bytes:
    body = bytes([slave & 0xFF, 0x10, (reg >> 8) & 0xFF, reg & 0xFF,
                  0x00, 0x01, 0x02, (value >> 8) & 0xFF, value & 0xFF])
    crc = crc16_modbus(body)
    return body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def _s16(v: int) -> int:
    return v - 65536 if v >= 32768 else v


def beep():
    try:
        import winsound
        winsound.Beep(1400, 250)
    except Exception:
        sys.stdout.write("\a")
        sys.stdout.flush()


class GripperError(RuntimeError):
    pass


class Gripper:
    def __init__(self, port: str = "COM8", speed: int = DEFAULT_SPEED,
                 timeout: float = 1.0, verbose: bool = True):
        self.port, self.speed, self.timeout, self.verbose = port, speed, timeout, verbose
        self.ser: serial.Serial | None = None

    def __enter__(self):
        self.ser = serial.Serial(self.port, 115200, timeout=self.timeout)
        time.sleep(0.1)
        self.ser.reset_input_buffer()
        return self

    def __exit__(self, *exc):
        if self.ser:
            self.ser.close()
        self.ser = None

    def _log(self, m):
        if self.verbose:
            print(m, flush=True)

    def _wr(self, reg: int, value: int, tries: int = 6) -> bool:
        if reg == 0x05:
            raise GripperError("reg0x05 is blocked")
        frame = build_write10(SLAVE, reg, value & 0xFFFF)
        for _ in range(tries):
            self.ser.reset_input_buffer()
            self.ser.write(frame)
            self.ser.flush()
            resp = read_exact(self.ser, 8, self.timeout)
            if len(resp) == 8 and resp[:6] == frame[:6]:
                return True
            time.sleep(0.25)
        return False

    def _rd(self, count: int, tries: int = 5):
        need = 3 + count * 2 + 2
        for _ in range(tries):
            frame = build_read_holding(SLAVE, 0, count)
            self.ser.reset_input_buffer()
            self.ser.write(frame)
            self.ser.flush()
            resp = read_exact(self.ser, need, self.timeout)
            if len(resp) == need and crc16_modbus(resp[:-2]) == int.from_bytes(resp[-2:], "little"):
                return tuple(int.from_bytes(resp[3 + 2 * i: 5 + 2 * i], "big") for i in range(count))
            time.sleep(0.3)
        return None

    def state(self) -> dict:
        w = self._rd(0x18)
        if w is None:
            raise GripperError("no response from slave 6")
        code = _s16(w[0x01])
        return {"code": code,
                "name": {CLOSED: "CLOSED", MOVING: "MOVING", OPEN: "OPEN"}.get(code, f"?{code}"),
                "err": _s16(w[0x02]), "mode": w[0x07], "run": w[0x08],
                "speed": w[0x0A], "target": _s16(w[0x0B]), "fault": _s16(w[0x17])}

    def verify(self) -> dict:
        w = self._rd(0x24)
        if w is None:
            raise GripperError("no response from slave 6")
        if (w[0x19], w[0x1B], w[0x1C]) != IDENTITY:
            raise GripperError(f"identity mismatch: {(w[0x19], w[0x1B], w[0x1C])}")
        if w[0x17]:
            raise GripperError(f"fault 0x{w[0x17]:04X}")
        return self.state()

    def prepare(self):
        if not self._wr(0x07, 1):
            raise GripperError("failed to set mode (reg0x07=1)")
        if not self._wr(0x0A, self.speed):
            raise GripperError("failed to set speed (reg0x0A)")
        if not self._wr(0x08, 1):
            raise GripperError("failed to enable (reg0x08=1)")
        time.sleep(0.3)

    def stop(self) -> bool:
        ok = self._wr(0x08, 0)
        self._log("stop (reg8=0): %s" % ("OK" if ok else "NO ACK"))
        return ok

    def _wait_code(self, want: int, timeout: float = 12.0) -> dict:
        """Silent wait, then single reads until the state code settles on `want`."""
        time.sleep(1.5)                      # never poll while it is moving
        deadline = time.monotonic() + timeout
        st = self.state()
        while st["code"] != want and time.monotonic() < deadline:
            if st["fault"]:
                raise GripperError(f"fault 0x{st['fault']:04X}")
            time.sleep(0.6)
            st = self.state()
        return st

    def _goto(self, target: int, want: int, label: str) -> dict:
        self.prepare()
        cur = self.state()
        self._log(f"{label}: state={cur['name']} -> target={target}")
        beep()
        if not self._wr(0x0B, target):
            raise GripperError("target write not acknowledged")
        st = self._wait_code(want)
        ok = st["code"] == want
        self._log(f"  -> {st['name']} ({'OK' if ok else 'TIMEOUT'}) err={st['err']} run={st['run']}")
        if not ok:
            raise GripperError(f"{label} did not reach the expected state")
        return st

    def open(self) -> dict:
        return self._goto(TARGET_OPEN, OPEN, "OPEN")

    def close(self) -> dict:
        return self._goto(TARGET_CLOSE, CLOSED, "CLOSE")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--speed", type=int, default=DEFAULT_SPEED)
    ap.add_argument("--yes", action="store_true", help="actually move (else dry run)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("stop")
    for name in ("open", "close"):
        s = sub.add_parser(name)
        s.add_argument("--yes", action="store_true", default=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    with Gripper(args.port, args.speed) as g:
        if args.cmd == "status":
            g.verify()
            st = g.state()
            print("slave6: state=%s code=%d err=%d mode=%d run=%d speed=%d target=%d fault=%d"
                  % (st["name"], st["code"], st["err"], st["mode"], st["run"],
                     st["speed"], st["target"], st["fault"]))
            return 0
        if args.cmd == "stop":
            g.verify(); g.stop(); time.sleep(0.5)
            print("after stop:", g.state()); return 0
        g.verify()
        if not getattr(args, "yes", False):
            print(f"dry run: would {args.cmd.upper()} the gripper (add --yes)")
            return 0
        st = g.open() if args.cmd == "open" else g.close()
        print("result:", st)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
