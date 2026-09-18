#!/usr/bin/env python
"""编码器 / 角度标定工具 —— 把"已知物理角度"和"寄存器计数"对应起来。

背景
----

位置伺服关节（J3/J4/J5）的 `reg0x01` 是内部霍尔换向累加出来的计数，
**零点来自上电瞬间**。所以"计数 -> 角度"的换算必须现场标定：

    收拢姿态（限位开关触发）= 0°   <- 参考点
    另一个已知角度            = X°   <- 标定点

⚠⚠ 最容易踩的坑：**两次读数之间不要断电**
------------------------------------------------

断电会让计数器重新归零。如果两次读数之间断了电，
两个读数就不在同一个参考系，算出来的换算率是错的（可能差一个数量级）。

所以：

1. 以目标 0 姿态上电；
2. 读完 0° 基准后，**在同一次上电内**把关节手转到已知角度；
3. 再读一次；
4. 要断电的话，先把所有关节都放回各自的 0 姿态。

每次上电给一个递增的 `--rev`，`--report` 只会在同一 `rev` 内互算。

用法
----

    # 1) 把机械臂放到已知角度（量角器/目测），记录一个标定点
    python analysis/encoder_calibration.py --joint 3 --deg 180 --rev 2

    # 2) 只看当前读数，不记录
    python analysis/encoder_calibration.py --joint 3 --status

    # 3) 打印已记录的全部标定点与推算出的换算率（按 rev 分组）
    python analysis/encoder_calibration.py --report

标定数据存在 `analysis/encoder_calibration.json`，是**追加式**的，
所以可以慢慢攒 0 / 90 / 180 / 250 多个点，再拟合。

⚠ 本工具只读寄存器，不会驱动电机。
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

from robot_arm import RobotArm  # noqa: E402
from robot_arm.registers import SERVO_CALIBRATION  # noqa: E402

STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "encoder_calibration.json")

DEFAULT_DEG_PER_COUNT = {3: 0.09, 4: 0.09, 5: 0.237}


def s16(v: int) -> int:
    return v - 0x10000 if v >= 0x8000 else v


def load() -> dict:
    if os.path.exists(STORE):
        with open(STORE, encoding="utf-8") as fh:
            return json.load(fh)
    return {"note": "J3/J4/J5 的 计数<->角度 标定点（收拢姿态 = 0°）", "points": []}


def save(data: dict) -> None:
    with open(STORE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def read_joint(arm: RobotArm, slave: int, repeats: int = 3):
    """稳定读取 pos/err（连读几次要求一致）。"""
    values = []
    for _ in range(repeats):
        w = arm.transport.try_read_registers(slave, 0x00, 0x19, tries=3)
        if w is None:
            raise RuntimeError("slave %d 无应答" % slave)
        values.append((s16(w[0x01]), s16(w[0x02]), w[0x07], w[0x08], w[0x18]))
        time.sleep(0.25)
    pos_set = set(v[0] for v in values)
    return {"pos": values[-1][0], "err": values[-1][1], "mode": values[-1][2],
            "run": values[-1][3], "limit": values[-1][4],
            "stable": len(pos_set) == 1, "samples": [v[0] for v in values]}


def report(data: dict) -> None:
    pts = data.get("points", [])
    if not pts:
        print("还没有任何标定点。")
        return
    # ⚠ 不同 `rev`（= 不同上电批次）的零点不同，**绝对不能混算**，
    # 所以按 (joint, rev) 分组。
    groups = {}
    for p in pts:
        groups.setdefault((p["joint"], p.get("rev", 0)), []).append(p)

    print("已记录标定点（按上电批次 rev 分组，同组内才可互算）：")
    for (joint, rev) in sorted(groups):
        rows = sorted(groups[(joint, rev)], key=lambda p: p["deg"])
        plain = [r for r in rows if not r.get("note") or "新零点" not in r.get("note", "")]
        focus = plain if len(plain) > 1 else rows
        ref = next((r for r in focus if abs(r["deg"]) < 1e-9), focus[0])
        print("  --- J%d  rev=%s  （参考点 %.2f° = %d count）---"
              % (joint, rev, ref["deg"], ref["pos_counts"]))
        for r in rows:
            dc = r["pos_counts"] - ref["pos_counts"]
            dd = r["deg"] - ref["deg"]
            rate = (dc / dd) if abs(dd) > 1e-9 else float("nan")
            print("      %8.2f°  %6d count   净 %+6d   %s"
                  % (r["deg"], r["pos_counts"], dc,
                     ("%.4f count/deg" % rate) if rate == rate else "—"))
        rates = []
        for r in focus:
            dd = r["deg"] - ref["deg"]
            if abs(dd) > 1e-9:
                rates.append((r["pos_counts"] - ref["pos_counts"]) / dd)
        if rates:
            avg = statistics.mean(rates)
            spread = max(rates) - min(rates)
            print("      => %.5f deg/count（%.4f count/deg），离散度 %.4f"
                  % (1 / avg if avg else float("nan"), avg, spread))
            print("         SDK 当前假设 %.5f deg/count"
                  % DEFAULT_DEG_PER_COUNT.get(joint, float("nan")))
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--joint", type=int, choices=(3, 4, 5), default=3)
    ap.add_argument("--deg", type=float, default=None,
                    help="该关节当前的**真实物理角度**（相对收拢姿态）")
    ap.add_argument("--status", action="store_true", help="只读当前计数")
    ap.add_argument("--report", action="store_true", help="打印已有标定点")
    ap.add_argument("--rev", type=int, default=1,
                    help="上电批次号：每次断电重上电要 +1（不同批次零点不同，不能混算）")
    args = ap.parse_args()

    data = load()

    if args.report:
        print(json.dumps({k: v for k, v in data.items() if k != "points"},
                         ensure_ascii=False, indent=2))
        report(data)
        return 0

    slave = args.joint
    with RobotArm(args.port, verbose=False) as arm:
        arm.verify_all(raise_on_error=False)
        st = read_joint(arm, slave)
        calib = SERVO_CALIBRATION.get(slave)
        dpc = calib.deg_per_count if calib else float("nan")
        print("J%d (slave %d): pos = %+d count (按当前 SDK 假设 = %+.3f deg)"
              % (args.joint, slave, st["pos"], st["pos"] * dpc))
        print("              err=%d mode=%d run=%d reg0x18(限位)=%d 稳定=%s %s"
              % (st["err"], st["mode"], st["run"], st["limit"], st["stable"],
                 st["samples"]))
        if not st["stable"]:
            print("\n⚠ 读数不稳定（机械臂可能在动或总线抖动），本次不记录。")
            return 2
        if args.status or args.deg is None:
            if args.deg is None and not args.status:
                print("\n提示：加 --deg <真实角度> 可把这次读数记为一个标定点。")
            return 0

        point = {
            "joint": args.joint, "slave": slave, "deg": float(args.deg),
            "pos_counts": st["pos"], "err": st["err"], "reg0x18": st["limit"],
            "rev": args.rev,
            "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        data.setdefault("points", []).append(point)
        save(data)
        print("\n已记录标定点: J%d %.2f° -> %d count" %
              (args.joint, args.deg, st["pos"]))
        print("写入 %s\n" % os.path.relpath(STORE, ROOT))
        report(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
