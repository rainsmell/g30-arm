#!/usr/bin/env python
"""关节 2（丝杆升降）逐脉冲放下 —— 限位开关不可信时唯一安全的下行方式。

为什么需要这个脚本
------------------

`analysis/JOINT2_CONTROL.md` §2.1 记录：**J1 松垮导致 J2 走到 0° 时压不到下限位开关**，
所以 `reg0x01` 分区值在两端都不可信，**不能**用"跑到限位自动停"。

J2 下行还有一个硬约束：连续下行超过 ~3.2 s 会触发固件超速保护 →
H 桥两路 100% 短接刹车 → 电流冲击 → **从站挂死，只能断电**。

所以标准做法是：**短脉冲（≤2.5 s）→ 停下 → 人看一眼 → 再来一次**。
本脚本把这件事做成可中断的循环，并且保证任何时候 Ctrl+C 都会立即关输出。

用法
----

    # 交互式：每发一个脉冲就停下问你，回车继续 / q 退出
    python examples/lower_joint2.py --port COM8

    # 批量：连发 3 个 1.5 秒脉冲
    python examples/lower_joint2.py --pulses 3 --seconds 1.5

    # 只是看看现在什么状态
    python examples/lower_joint2.py --status

⚠ 运行前请确认机械臂下方没有东西也没有人。本脚本会真的驱动电机。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from robot_arm import ArmError, RobotArm, SafetyError  # noqa: E402
from robot_arm.registers import REG_ENABLE, REG_TARGET  # noqa: E402

DOWN, UP = 0, 1
MAX_PULSE = 2.5          # 秒；超过 ~3.2 s 会挂死从站


def show(joint, tag: str):
    st = joint.state()
    print("  [%s] zone=%d (%s)  运动指示=%d  使能=%d  方向=%d  故障=%d"
          % (tag, st.zone, st.zone_name.split(" ")[0], st.motion, st.run,
             st.direction, st.fault))
    print("       ^ 注意：zone 现在不可信（J1 松垮），请以目测为准")
    return st


def one_pulse(joint, direction: int, seconds: float, settle: float = 1.4) -> None:
    """发一个脉冲。Ctrl+C 时也会在 finally 里关掉输出。"""
    name = "下行" if direction == DOWN else "上行"
    print("  发送 %s 脉冲 %.2fs ..." % (name, seconds))
    try:
        if not joint.state().run:
            joint.enable()
            time.sleep(0.3)
        joint.write(REG_TARGET, direction)
        time.sleep(seconds)
    except KeyboardInterrupt:
        print("\n  收到中断，立即关输出 ...")
        raise
    finally:
        # 无论正常结束还是被 Ctrl+C，都必须关掉驱动
        try:
            joint.write(REG_ENABLE, 0)
        except ArmError as exc:
            print("  !! 关输出失败（从站可能已挂死）: %s" % exc)
    time.sleep(settle)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--direction", choices=("down", "up"), default="down")
    ap.add_argument("--seconds", type=float, default=1.5,
                    help="单个脉冲时长，默认 1.5s，上限 %.1fs" % MAX_PULSE)
    ap.add_argument("--pulses", type=int, default=0,
                    help="0 = 交互式（每次回车发一个）；N = 连发 N 个")
    ap.add_argument("--status", action="store_true", help="只读状态后退出")
    args = ap.parse_args()

    if args.seconds <= 0 or args.seconds > MAX_PULSE:
        print("REFUSED: --seconds 必须在 (0, %.1f] 内（更长会挂死从站）" % MAX_PULSE,
              file=sys.stderr)
        return 2

    direction = DOWN if args.direction == "down" else UP

    with RobotArm(args.port, verbose=False) as arm:
        arm.j2.verify()
        show(arm.j2, "起始")

        if args.status:
            return 0

        try:
            index = 0
            while True:
                index += 1
                if args.pulses:
                    if index > args.pulses:
                        break
                else:
                    answer = input("\n回车 = 发第 %d 个脉冲；q = 退出 > " % index).strip().lower()
                    if answer in ("q", "quit", "exit"):
                        break

                one_pulse(arm.j2, direction, args.seconds)
                show(arm.j2, "第 %d 个脉冲后" % index)
                print("       -> 如果已经到位，按 Ctrl+C 或输 q 退出；"
                      "若方向不对请立刻停手")
        except KeyboardInterrupt:
            print("\n已中断。")
        finally:
            try:
                arm.j2.stop()
                print("\n收尾: 输出已关闭 (reg0x08=0)")
            except ArmError as exc:
                print("\n!! 收尾关输出失败: %s" % exc, file=sys.stderr)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SafetyError as exc:
        print("REFUSED:", exc, file=sys.stderr)
        raise SystemExit(2)
    except ArmError as exc:
        print("ERROR:", exc, file=sys.stderr)
        raise SystemExit(1)
