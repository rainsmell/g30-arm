#!/usr/bin/env python
"""完整动作序列示例（演示 SDK 的编排能力）。

用法::

    python examples/demo_sequence.py --dry-run          # 先看它打算做什么
    python examples/demo_sequence.py --yes              # 真正执行
    python examples/demo_sequence.py --list             # 列出所有步骤
    python examples/demo_sequence.py --only wrist --yes

⚠ 本脚本会让整条机械臂运动。执行前请：
  1. 确认工作范围内无障碍物、无人；
  2. 确认机械臂已固定在台面上；
  3. 知道"停止"会让伺服关节失去保持力矩（先支撑好）。

默认的 ``--dry-run`` 行为：只打印将要下发的寄存器写。
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Callable, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from robot_arm import ArmError, RobotArm, SafetyError  # noqa: E402

#: (名称, 说明, 动作函数)
Step = Tuple[str, str, Callable[[RobotArm], None]]


def build_steps(args) -> List[Step]:
    return [
        ("verify", "校验 6 个关节的固件身份",
         lambda arm: arm.verify_all()),
        ("lift-up", "大臂抬升 2.5 秒（到上限位自动停）",
         lambda arm: arm.j2.up(2.5)),
        ("elbow", "肘部(3) 走到 %+.1f 度" % args.elbow,
         lambda arm: arm.j3.move_to_deg(args.elbow)),
        ("forearm", "前臂(4) 走到 %+.1f 度" % args.forearm,
         lambda arm: arm.j4.move_to_deg(args.forearm)),
        ("wrist", "腕部 roll(5) 走到 %.1f 度（90 = 垂直）" % args.wrist,
         lambda arm: arm.j5.move_to_deg(args.wrist)),
        ("grip", "夹爪：张开 -> 闭合",
         lambda arm: (arm.j6.open(), arm.j6.close())),
        ("release", "伺服关节回 0 度并停止",
         lambda arm: (arm.j3.move_to_deg(0.0), arm.j4.move_to_deg(0.0),
                      arm.j5.move_to_deg(0.0), arm.j3.stop(), arm.j4.stop(),
                      arm.j5.stop())),
        ("estop", "关闭所有关节输出",
         lambda arm: arm.estop()),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--yes", action="store_true", help="真正执行（否则 dry-run）")
    ap.add_argument("--list", action="store_true", help="只列出步骤")
    ap.add_argument("--only", action="append", default=None,
                    help="只执行指定步骤（可重复）")
    ap.add_argument("--speed", type=int, default=250)
    ap.add_argument("--elbow", type=float, default=-20.0)
    ap.add_argument("--forearm", type=float, default=10.0)
    ap.add_argument("--wrist", type=float, default=90.0)
    ap.add_argument("--keep-enabled", action="store_true",
                    help="结束后不停用伺服关节（默认最后会失能）")
    args = ap.parse_args()

    dry_run = not args.yes
    steps = build_steps(args)

    if args.list:
        for name, desc, _ in steps:
            print("  %-10s %s" % (name, desc))
        return 0

    selected = steps
    if args.only:
        wanted = set(args.only)
        selected = [s for s in steps if s[0] in wanted]
        missing = wanted - {s[0] for s in selected}
        if missing:
            print("unknown step(s): %s" % sorted(missing), file=sys.stderr)
            return 2

    if dry_run:
        print("[DRY RUN] 不会下发任何写命令；下面是要执行的步骤：")
        for name, desc, _ in selected:
            print("  %-10s %s" % (name, desc))
        print()

    try:
        with RobotArm(args.port, dry_run=dry_run, verbose=True) as arm:
            arm.j3.speed = args.speed
            arm.j4.speed = args.speed
            arm.j5.speed = args.speed

            for name, desc, action in selected:
                print("\n--- %s: %s ---" % (name, desc))
                try:
                    action(arm)
                except SafetyError as exc:
                    print("  REFUSED: %s" % exc, file=sys.stderr)
                    return 2
                except ArmError as exc:
                    print("  FAILED: %s" % exc, file=sys.stderr)
                    print("  尝试紧急停止 ...")
                    print("  estop ->", arm.estop())
                    return 1

            if not args.keep_enabled and "release" in {s[0] for s in selected}:
                print("\n提示：伺服关节已失能，机械臂不再有保持力矩。")

            if dry_run:
                print("\n[DRY RUN] 实际会下发的写命令：")
                for slave, reg, value in arm.transport.clear_planned_writes():
                    print("  slave %d  reg 0x%02X = 0x%04X" % (slave, reg, value))
    except SafetyError as exc:
        print("REFUSED:", exc, file=sys.stderr)
        return 2
    except ArmError as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
