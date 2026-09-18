"""命令行接口：``python -m robot_arm ...``

示例::

    python -m robot_arm status
    python -m robot_arm verify
    python -m robot_arm estop
    python -m robot_arm read --slave 3 --start 0x00 --count 0x18
    python -m robot_arm j2 up --seconds 2 --yes
    python -m robot_arm j3 move --rel-deg -20 --yes
    python -m robot_arm j5 move --deg 90 --yes
    python -m robot_arm j6 open --yes

除 ``status`` / ``verify`` / ``estop`` / ``read`` 外，动作命令都需要 ``--yes``，
否则只做试运行并打印将要执行的内容。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from .arm import RobotArm
from .exceptions import ArmError, SafetyError


def _dump(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _common_parent() -> argparse.ArgumentParser:
    """公共选项。

    做成 ``parents=[...]`` 后同时挂在主解析器和每个子解析器上，
    因此 ``--port`` 放在子命令**前后都可以**（``default=SUPPRESS``
    保证未指定的那侧不会覆盖已指定的值）。
    """
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--port", default=argparse.SUPPRESS, help="串口（默认 COM8）")
    parent.add_argument("--baud", type=int, default=argparse.SUPPRESS,
                        help="波特率（默认 115200）")
    parent.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                        help="不真正下发写命令")
    parent.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS,
                        help="打印总线收发明细")
    parent.add_argument("--beep", action="store_true", default=argparse.SUPPRESS,
                        help="动作前播放提示音")
    return parent


def _motion_yes(parser: argparse.ArgumentParser, action: str):
    parser.add_argument("--yes", action="store_true", default=argparse.SUPPRESS,
                        help="确认执行 %s（否则只试运行）" % action)


def build_parser() -> argparse.ArgumentParser:
    common = _common_parent()
    ap = argparse.ArgumentParser(
        prog="robot-arm",
        description="6 轴机械臂控制 SDK 命令行",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common],
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name: str, **kwargs):
        return sub.add_parser(name, parents=[common], **kwargs)

    add("status", help="只读快照（所有关节）")
    add("verify", help="校验所有关节的固件身份与故障")
    add("estop", help="紧急停止：关闭所有关节输出")

    rd = add("read", help="直接读寄存器")
    rd.add_argument("--slave", type=int, required=True)
    rd.add_argument("--start", type=lambda x: int(x, 0), default=0)
    rd.add_argument("--count", type=lambda x: int(x, 0), default=0x18)

    # ---- 关节 1 ----
    j1 = add("j1", help="关节 1 底座回转")
    j1s = j1.add_subparsers(dest="sub", required=True)
    j1s.add_parser("status", parents=[common])
    j1s.add_parser("stop", parents=[common])
    j1r = j1s.add_parser("rotate", parents=[common])
    j1r.add_argument("--dir", default="a", help="a / b / cw / ccw")
    j1r.add_argument("--seconds", type=float, default=2.5)
    _motion_yes(j1r, "旋转")

    # ---- 关节 2 ----
    j2 = add("j2", help="关节 2 丝杆升降")
    j2s = j2.add_subparsers(dest="sub", required=True)
    j2s.add_parser("status", parents=[common])
    j2s.add_parser("stop", parents=[common])
    j2s.add_parser("enable", parents=[common])
    for name, default in (("up", 2.0), ("down", 1.5)):
        p = j2s.add_parser(name, parents=[common])
        p.add_argument("--seconds", type=float, default=default)
        _motion_yes(p, "脉冲")

    # ---- 关节 3/4/5 ----
    for jname, label in (("j3", "肘部伺服"), ("j4", "前臂伺服"), ("j5", "腕部 roll")):
        jp = add(jname, help="关节 %s %s" % (jname[1:], label))
        sp = jp.add_subparsers(dest="sub", required=True)
        sp.add_parser("status", parents=[common])
        sp.add_parser("stop", parents=[common])
        sp.add_parser("enable", parents=[common])
        mv = sp.add_parser("move", parents=[common])
        mv.add_argument("--speed", type=int, default=None)
        mv.add_argument("--limit-lo", type=int, default=None)
        mv.add_argument("--limit-hi", type=int, default=None)
        group = mv.add_mutually_exclusive_group(required=True)
        group.add_argument("--deg", type=float, help="绝对角度（相对上电零点）")
        group.add_argument("--counts", type=int, help="绝对计数目标")
        group.add_argument("--rel-deg", type=float, help="相对角度")
        group.add_argument("--rel-counts", type=int, help="相对计数")
        _motion_yes(mv, "移动")

    # ---- 关节 6 ----
    j6 = add("j6", help="关节 6 夹爪")
    j6s = j6.add_subparsers(dest="sub", required=True)
    j6s.add_parser("status", parents=[common])
    j6s.add_parser("stop", parents=[common])
    for name in ("open", "close"):
        p = j6s.add_parser(name, parents=[common])
        p.add_argument("--speed", type=int, default=None)
        _motion_yes(p, "夹爪动作")

    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    kwargs = {
        "dry_run": getattr(args, "dry_run", False),
        "verbose": getattr(args, "verbose", False),
        "beep": getattr(args, "beep", False),
    }
    port = getattr(args, "port", None)
    baud = getattr(args, "baud", None)
    if port:
        kwargs["port"] = port
    if baud:
        kwargs["baudrate"] = baud

    try:
        with RobotArm(**kwargs) as arm:
            return _dispatch(arm, args)
    except SafetyError as exc:
        print("REFUSED (safety): %s" % exc, file=sys.stderr)
        return 2
    except ArmError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted - attempting estop", file=sys.stderr)
        try:
            with RobotArm(**kwargs) as arm:
                _dump(arm.estop())
        except Exception:
            pass
        return 130


def _dispatch(arm: RobotArm, args) -> int:
    cmd = args.cmd

    if cmd == "status":
        _dump(arm.status())
        return 0

    if cmd == "verify":
        results = arm.verify_all(raise_on_error=False)
        _dump(results)
        return 0 if all(r.get("ok") for r in results.values()) else 1

    if cmd == "estop":
        _dump(arm.estop())
        return 0

    if cmd == "read":
        words = arm.transport.read_registers(args.slave, args.start, args.count)
        _dump({"slave": args.slave, "start": hex(args.start), "count": args.count,
               "words": ["0x%04X" % w for w in words]})
        return 0

    if cmd == "j1":
        return _dispatch_j1(arm, args)
    if cmd == "j2":
        return _dispatch_j2(arm, args)
    if cmd in ("j3", "j4", "j5"):
        return _dispatch_servo(arm, args)
    if cmd == "j6":
        return _dispatch_j6(arm, args)
    raise SystemExit("unhandled command %r" % cmd)


def _dispatch_j1(arm: RobotArm, args) -> int:
    joint = arm.j1
    if args.sub == "status":
        joint.verify()
        print(joint.state())
        return 0
    if args.sub == "stop":
        joint.stop()
        print("stopped", joint.state())
        return 0
    if not getattr(args, "yes", False):
        print("dry run: would rotate dir=%s for %.2fs (add --yes)" % (args.dir, args.seconds))
        return 0
    if not joint.transport.dry_run:
        joint.verify()
    print(joint.rotate(args.dir, args.seconds))
    return 0


def _dispatch_j2(arm: RobotArm, args) -> int:
    joint = arm.j2
    if args.sub == "status":
        joint.verify()
        print(joint.state(), "|", joint.state().zone_name)
        return 0
    if args.sub == "stop":
        joint.stop()
        print("stopped")
        return 0
    if args.sub == "enable":
        joint.enable()
        print("enabled")
        return 0
    if not getattr(args, "yes", False):
        print("dry run: would pulse %s for %.2fs (add --yes)" % (args.sub.upper(), args.seconds))
        return 0
    if not joint.transport.dry_run:
        joint.verify()
    state = joint.up(args.seconds) if args.sub == "up" else joint.down(args.seconds)
    print(state, "|", state.zone_name)
    return 0


def _dispatch_servo(arm: RobotArm, args) -> int:
    joint = arm[args.cmd]
    if args.sub == "status":
        joint.verify()
        print(joint.state())
        return 0
    if args.sub == "stop":
        joint.stop()
        print("stopped (drive removed; support the arm first)")
        return 0
    if args.sub == "enable":
        joint.enable()
        print("enabled")
        return 0

    if args.limit_lo is not None and args.limit_hi is not None:
        joint.set_limits(args.limit_lo, args.limit_hi)
    if args.speed is not None:
        joint.set_speed(args.speed)

    if args.deg is not None:
        target_desc = "abs %.2f deg" % args.deg
    elif args.counts is not None:
        target_desc = "abs %d counts" % args.counts
    elif args.rel_deg is not None:
        target_desc = "rel %.2f deg" % args.rel_deg
    else:
        target_desc = "rel %d counts" % args.rel_counts

    if not getattr(args, "yes", False):
        print("dry run: would move %s to %s (add --yes)" % (joint.name, target_desc))
        return 0
    if not joint.transport.dry_run:
        joint.verify()

    if args.deg is not None:
        state = joint.move_to_deg(args.deg)
    elif args.counts is not None:
        state = joint.move_to_counts(args.counts)
    elif args.rel_deg is not None:
        state = joint.move_rel_deg(args.rel_deg)
    else:
        state = joint.move_rel_counts(args.rel_counts)
    print(state)
    return 0


def _dispatch_j6(arm: RobotArm, args) -> int:
    joint = arm.j6
    if args.sub == "status":
        joint.verify()
        print(joint.state())
        return 0
    if args.sub == "stop":
        joint.stop()
        print("stopped (grip force released)")
        return 0
    if args.speed is not None:
        joint.speed = args.speed
    if not getattr(args, "yes", False):
        print("dry run: would %s the gripper (add --yes)" % args.sub.upper())
        return 0
    if not joint.transport.dry_run:
        joint.verify()
    state = joint.open() if args.sub == "open" else joint.close()
    print(state)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
