#!/usr/bin/env python
"""夹爪定时开度标定 —— 建立「运行时长 -> 钳口实际间距」的对应关系。

为什么必须手动标定
------------------

实测（2026-09-13）证明 J6 的 ``reg0x01`` **只有 0 / 45 / 90 三态**：

* ``0``  = 全闭
* ``45`` = **在途中**（不是"半开"！T=0.5s ~ 3.0s 都是它）
* ``90`` = 全开，且 ``reg0x18=1``（**上限位开关触发**）

也就是说**固件不提供连续开度反馈**，中间开度只能用"跑多久"来控制。
而"跑 T 秒到底开了多少毫米"只有拿卡尺量出来才知道 —— 就是本工具做的事。

用法
----

    # 交互式标定（推荐）：每个时间点跑一次，量完输入读数
    python analysis/gripper_timing_calibration.py --times 0.5 1 1.5 2 2.5 3 3.5

    # 只跑其中几个点
    python analysis/gripper_timing_calibration.py --times 2 3

    # 看已有数据 + 拟合结果
    python analysis/gripper_timing_calibration.py --report

    # 只看当前开口（不进标定流程）
    python analysis/gripper_timing_calibration.py --status

标定数据存在 ``analysis/gripper_timing.json``，**追加式**。

⚠ 运行前请确认夹爪里没有东西、也不会夹到人。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from robot_arm import ArmError, RobotArm  # noqa: E402
from robot_arm.joints.gripper import (  # noqa: E402
    FULL_OPEN_COUNTS, FULL_TRAVEL_SECONDS,
)

STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "gripper_timing.json")
SETTLE = 6.0        # 回到全闭/全开的等待时间（全程静默，不读）


def load() -> dict:
    if os.path.exists(STORE):
        with open(STORE, encoding="utf-8") as fh:
            return json.load(fh)
    return {"note": "J6 夹爪：运行时长 -> 钳口间距（mm）标定点", "points": []}


def save(data: dict) -> None:
    with open(STORE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def report(data: dict) -> None:
    pts = [p for p in data.get("points", []) if p.get("mm") is not None]
    if not pts:
        print("还没有带测量值的标定点。")
        return
    pts.sort(key=lambda p: p["seconds"])
    print("已记录标定点（运行时长 -> 钳口间距）：")
    print("   时长(s)   实测间距(mm)   每 0.5s 增量   相对全开")
    prev = None
    full = max(p["mm"] for p in pts)
    for p in pts:
        delta = "" if prev is None else "%+.1f" % (p["mm"] - prev)
        ratio = (p["mm"] / full * 100) if full else 0
        print("   %6.2f   %10.1f   %12s   %5.0f%%"
              % (p["seconds"], p["mm"], delta, ratio))
        prev = p["mm"]
    print()
    # 线性拟合（过原点会更稳，但先给最小二乘）
    xs = [p["seconds"] for p in pts]
    ys = [p["mm"] for p in pts]
    n = len(xs)
    if n >= 2:
        mx, my = statistics.mean(xs), statistics.mean(ys)
        den = sum((x - mx) ** 2 for x in xs)
        if den > 1e-9:
            a = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
            b = my - a * mx
            print("   拟合: 间距 ≈ %.2f * 时长 %+.2f  (mm)" % (a, b))
            print("   -> 要开到 X mm，大约需要 %.2f s/X" % (1 / a) if a else "")
            print("   -> 全行程 %.1f mm 约需 %.2f s" % (full, (full - b) / a) if a else "")
    print("   参考: 固件锚点 T=0 -> 全闭, T>=%.1fs -> 全开（限位开关）\n"
          % FULL_TRAVEL_SECONDS)


def move(joint, target: int, seconds: float) -> None:
    """写目标 -> 静默运行 -> 停（全程不读寄存器）。"""
    joint.prepare()
    joint.write(0x0B, target)
    time.sleep(seconds)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--speed", type=int, default=100,
                    help="夹爪速度上限（默认 100，与标定基准一致）")
    ap.add_argument("--times", type=float, nargs="+", default=None,
                    help="要标定的运行时长的列表（秒）")
    ap.add_argument("--report", action="store_true", help="只看已有标定数据")
    ap.add_argument("--status", action="store_true", help="只读当前状态")
    ap.add_argument("--no-reset", action="store_true",
                    help="不从全闭开始（默认每个点都先回到全闭，保证一致）")
    args = ap.parse_args()

    data = load()
    if args.report:
        report(data)
        return 0

    with RobotArm(args.port, verbose=False) as arm:
        arm.verify_all(raise_on_error=False)
        j = arm.j6
        j.speed = args.speed

        if args.status:
            st = j.state()
            print("J6: code=%d (%s)  load=%d  mode=%d run=%d speed=%d"
                  % (st.counts, st.name, st.err, st.mode, st.run, st.speed))
            print("锚点: T=0 -> 全闭；T>=%.1fs -> 全开（reg0x18 限位）"
                  % FULL_TRAVEL_SECONDS)
            return 0

        times = args.times or [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
        print("=== 夹爪定时开度标定（速度 %d）===" % args.speed)
        print("每个时间点会先回到全闭，再张开 T 秒后停。")
        print("请在停稳后用卡尺测量**钳口内侧间距**，输入毫米数。")
        print("（直接回车 = 跳过该点；输入 q = 结束）\n")

        try:
            for T in times:
                if not args.no_reset:
                    print("  回到全闭 ...", flush=True)
                    move(j, 0, SETTLE)
                print("  张开 %.2f 秒 ..." % T, flush=True)
                j.prepare()
                j.write(0x0B, FULL_OPEN_COUNTS)
                time.sleep(T)
                j.write(0x08, 0)                 # ★ 停
                time.sleep(1.2)
                st = j.state()
                print("  已停。code=%d (%s)  load=%d"
                      % (st.counts, st.name, st.err), flush=True)

                answer = input("  钳口间距(mm) > ").strip()
                if answer.lower() in ("q", "quit", "exit"):
                    break
                if not answer:
                    print("  跳过\n")
                    continue
                try:
                    mm = float(answer)
                except ValueError:
                    print("  不是数字，跳过\n")
                    continue
                data["points"].append({
                    "seconds": float(T), "mm": mm, "speed": args.speed,
                    "code": st.counts,
                    "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                })
                save(data)
                print("  已记录\n")
        except KeyboardInterrupt:
            print("\n已中断。")
        finally:
            try:
                j.stop()
                print("收尾: 夹爪驱动已关闭（reg0x08=0，夹持力已释放）")
            except ArmError as exc:
                print("收尾关输出失败: %s" % exc, file=sys.stderr)

    report(load())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ArmError as exc:
        print("ERROR:", exc, file=sys.stderr)
        raise SystemExit(1)
