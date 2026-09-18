#!/usr/bin/env python
"""位置伺服关节移动示例（关节 3 / 4 / 5）。

用法::

    python examples/move_servo.py --port COM8 --joint 3 --deg 20
    python examples/move_servo.py --joint 5 --deg 90 --speed 200
    python examples/move_servo.py --joint 4 --rel-deg -15

⚠ 会真的让关节运动。运行前请确认周围无障碍、机械臂已支撑好。
   停止（``reg0x08=0``）会让关节**失去保持力矩**。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from robot_arm import ArmError, RobotArm, SafetyError  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--joint", type=int, choices=(3, 4, 5), default=3)
    ap.add_argument("--speed", type=int, default=None)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--deg", type=float, help="绝对角度（相对上电零点）")
    group.add_argument("--rel-deg", type=float, help="相对角度")
    group.add_argument("--counts", type=int, help="绝对计数")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        with RobotArm(args.port, dry_run=args.dry_run, verbose=True) as arm:
            joint = arm[args.joint]
            joint.verify()
            print("before:", joint.state())

            if args.speed:
                joint.set_speed(args.speed)
            if args.deg is not None:
                state = joint.move_to_deg(args.deg)
            elif args.rel_deg is not None:
                state = joint.move_rel_deg(args.rel_deg)
            else:
                state = joint.move_to_counts(args.counts)
            print("after :", state)
    except SafetyError as exc:
        print("REFUSED:", exc, file=sys.stderr)
        return 2
    except ArmError as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
