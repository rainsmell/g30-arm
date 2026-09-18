#!/usr/bin/env python
"""Joint 2 (lead-screw boom) pulse controller for the 2/3 link-arm board (fw2).

Mechanism (user description): motor -> lead screw -> slider -> rotates joint 2,
like an excavator boom.  Full travel ~90 degrees.

Verified interface -- see JOINT2_CONTROL.md:

  reg0x00  R   speed-ish (reads 0)
  reg0x01  R   position ZONE only: 0 / 45 / 90   (no continuous sensor!)
  reg0x02  R   motion indicator: ~130..155 while moving, 0 when idle
  reg0x07  R   mode = 0 (joint 2), differs from joint 3 (1)
  reg0x08  R/W enable flag.  Writing 0 removes drive.
  reg0x0A  -   unused on joint 2
  reg0x0B  W   direction+start: 1 = UP, 0 = DOWN
  reg0x17  R   fault

Control constraints learned the hard way:
  * UP  (reg0x0B=1) is safe and repeatable -- it ends on the upper limit switch.
  * DOWN (reg0x0B=0) has NO limit switch.  If left running longer than ~3.2 s the
    firmware hits its over-speed protection which applies a HARD BRAKE
    (both H-bridge channels at 100% = short the motor); that current spike makes
    the slave hang, and only a power cycle brings it back.
  * Therefore DOWN is done as SHORT PULSES (default cap 2.5 s) followed by reg8=0.
  * Position feedback is only a zone (0/45/90), so fine positioning needs eyes.

CLI:
  python joint2.py status
  python joint2.py up --seconds 2 --yes
  python joint2.py down --seconds 1.5 --yes
  python joint2.py stop
"""
from __future__ import annotations

import argparse
import sys
import time

import serial

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from safe_rs485_probe import build_read_holding, crc16_modbus, read_exact

IDENTITY = (2, 0x67C6, 0xD81D)
MAX_PULSE = 2.5          # seconds; beyond ~3.2 s the firmware faults and the slave hangs
UP, DOWN = 1, 0


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
        winsound.Beep(1400, 300)
    except Exception:
        sys.stdout.write("\a")
        sys.stdout.flush()


class Joint2Error(RuntimeError):
    pass


class Joint2:
    def __init__(self, port: str = "COM8", max_pulse: float = MAX_PULSE,
                 timeout: float = 1.0, verbose: bool = True):
        self.port, self.max_pulse, self.timeout, self.verbose = port, max_pulse, timeout, verbose
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

    # ---- io -------------------------------------------------------------
    def _wr(self, reg: int, value: int, tries: int = 6) -> bool:
        if reg == 0x05:
            raise Joint2Error("reg0x05 is blocked")
        frame = build_write10(2, reg, value & 0xFFFF)
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
            frame = build_read_holding(2, 0, count)
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
            raise Joint2Error("no response from slave 2 (hung? needs power cycle)")
        return {"pos": _s16(w[0x01]), "vel": _s16(w[0x00]), "err": _s16(w[0x02]),
                "mode": w[7], "run": w[8], "dir": _s16(w[0x0B]), "fault": _s16(w[0x17])}

    def verify(self) -> dict:
        w = self._rd(0x24)
        if w is None:
            raise Joint2Error("no response from slave 2 (hung? needs power cycle)")
        if (w[0x19], w[0x1B], w[0x1C]) != IDENTITY:
            raise Joint2Error(f"identity mismatch: {(w[0x19], w[0x1B], w[0x1C])}")
        if w[0x17]:
            raise Joint2Error(f"fault 0x{w[0x17]:04X}")
        return self.state()

    # ---- control --------------------------------------------------------
    def stop(self) -> bool:
        ok = self._wr(0x08, 0)
        self._log("stop (reg8=0): %s" % ("OK" if ok else "NO ACK"))
        return ok

    def enable(self) -> bool:
        return self._wr(0x08, 1)

    def pulse(self, direction: int, seconds: float) -> dict:
        """Drive in one direction for `seconds`, then stop.  Never exceeds max_pulse."""
        if direction not in (UP, DOWN):
            raise Joint2Error("direction must be UP(1) or DOWN(0)")
        if not 0 < seconds <= self.max_pulse:
            raise Joint2Error(f"seconds must be in (0, {self.max_pulse}] -- longer runs fault the drive")
        name = "UP" if direction == UP else "DOWN"
        st = self.state()
        self._log(f"{name} pulse {seconds:.2f}s from pos={st['pos']} dir={st['dir']} run={st['run']}")
        if not st["run"]:
            self.enable()
            time.sleep(0.3)
        beep()
        if not self._wr(0x0B, direction):
            raise Joint2Error("direction write not acknowledged")
        time.sleep(seconds)
        self._wr(0x08, 0)                      # stop immediately after the pulse
        time.sleep(1.2)                        # settle, no reads while moving
        after = self.state()
        self._log("  -> pos=%d err=%d run=%d" % (after["pos"], after["err"], after["run"]))
        return after

    def up(self, seconds: float = 2.0):
        return self.pulse(UP, seconds)

    def down(self, seconds: float = 1.5):
        return self.pulse(DOWN, seconds)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--yes", action="store_true", help="actually move (else dry run)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("stop")
    sub.add_parser("enable")
    for name in ("up", "down"):
        s = sub.add_parser(name)
        s.add_argument("--seconds", type=float, default=2.0 if name == "up" else 1.5)
        s.add_argument("--yes", action="store_true", default=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    with Joint2(args.port) as j:
        if args.cmd == "status":
            j.verify()
            st = j.state()
            print("slave2: pos(zone)=%d err=%d run=%d dir=%d mode=%d fault=%d"
                  % (st["pos"], st["err"], st["run"], st["dir"], st["mode"], st["fault"]))
            return 0
        if args.cmd == "stop":
            j.verify(); j.stop(); time.sleep(0.5)
            print("after stop:", j.state()); return 0
        if args.cmd == "enable":
            j.verify(); j.enable(); time.sleep(0.3)
            print("after enable:", j.state()); return 0
        j.verify()
        seconds = args.seconds
        if not 0 < seconds <= j.max_pulse:
            print(f"REFUSED: --seconds must be in (0, {j.max_pulse}]"); return 2
        if not getattr(args, "yes", False):
            print(f"dry run: would pulse {args.cmd.upper()} for {seconds:.2f}s (add --yes)")
            return 0
        st = j.up(seconds) if args.cmd == "up" else j.down(seconds)
        print("result:", st)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
