"""机械臂门面对象：一块总线 + 六个关节。

典型用法::

    from robot_arm import RobotArm

    with RobotArm("COM8", verbose=True) as arm:
        print(arm.status())              # 只读快照
        arm.verify_all()                 # 身份校验（写命令前必须做）
        arm.j3.move_to_deg(45)           # 肘部
        arm.j5.move_to_deg(90)           # 腕部 roll
        arm.j6.close()                   # 夹爪
        arm.j2.up(2.0)                   # 大臂抬升
        arm.j1.rotate("a", 2.5)          # 底座回转

    # 试运行（不真正下发写命令）
    with RobotArm("COM8", dry_run=True) as arm:
        arm.j3.move_to_deg(45)
        print(arm.transport.clear_planned_writes())

关节别名：

===========  =========  =========================================
属性          从站        说明
===========  =========  =========================================
``j1``/``base``      1   底座回转（fw1，无位置闭环）
``j2``/``lift``      2   丝杆升降（方向 + 限位）
``j3``/``elbow``     3   肘部位置伺服
``j4``/``forearm``   4   前臂位置伺服
``j5``/``wrist``     5   腕部 roll 位置伺服
``j6``/``gripper``   6   夹爪（两态）
===========  =========  =========================================
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .exceptions import ArmError
from .joints import GripperJoint, LiftJoint, ServoJoint, YawJoint
from .registers import (
    GRIPPER_DEFAULT_SPEED,
    IDENTITIES,
    SERVO_CALIBRATION,
    SERVO_DEFAULT_SPEED,
    SERVO_REQUIRES_PREPARE,
)
from .transport import DEFAULT_BAUDRATE, DEFAULT_PORT, ModbusTransport
from .util import Beeper, Logger, default_logger


class RobotArm:
    """六轴机械臂的高层接口（一个串口、一条总线）。"""

    def __init__(
        self,
        port: str = DEFAULT_PORT,
        *,
        baudrate: int = DEFAULT_BAUDRATE,
        timeout: float = 1.0,
        retries: int = 5,
        retry_delay: float = 0.25,
        dry_run: bool = False,
        verbose: bool = False,
        beep: bool = False,
        logger: Optional[Logger] = None,
        transport: Optional[ModbusTransport] = None,
        trace=None,
        servo_speeds: Optional[Dict[int, int]] = None,
        servo_limits: Optional[Dict[int, Tuple[int, int]]] = None,
    ):
        self.verbose = verbose
        self._log: Logger = logger or default_logger(verbose)
        self.transport = transport or ModbusTransport(
            port,
            baudrate,
            timeout=timeout,
            retries=retries,
            retry_delay=retry_delay,
            dry_run=dry_run,
            logger=self._log if verbose else None,
            trace=trace,
        )
        beeper = Beeper(enabled=beep)
        self.beeper = beeper

        servo_speeds = dict(servo_speeds or {})
        servo_limits = dict(servo_limits or {})

        # --- 关节 1 ---
        self.j1 = YawJoint(self.transport, name="j1/base", beeper=beeper, logger=self._log)
        self.j1.identity = IDENTITIES[1]

        # --- 关节 2 ---
        self.j2 = LiftJoint(self.transport, name="j2/lift", beeper=beeper, logger=self._log)
        self.j2.identity = IDENTITIES[2]

        # --- 关节 3/4/5（位置伺服） ---
        self.j3 = self._make_servo(3, "j3/elbow", beeper, servo_speeds, servo_limits)
        self.j4 = self._make_servo(4, "j4/forearm", beeper, servo_speeds, servo_limits)
        self.j5 = self._make_servo(5, "j5/wrist", beeper, servo_speeds, servo_limits)

        # --- 关节 6 ---
        self.j6 = GripperJoint(
            self.transport,
            speed=int(servo_speeds.get(6, GRIPPER_DEFAULT_SPEED)),
            name="j6/gripper",
            beeper=beeper,
            logger=self._log,
        )
        self.j6.identity = IDENTITIES[6]

        # 别名
        self.base = self.j1
        self.lift = self.j2
        self.elbow = self.j3
        self.forearm = self.j4
        self.wrist = self.j5
        self.gripper = self.j6

    def _make_servo(self, slave, name, beeper, speeds, limits) -> ServoJoint:
        calib = SERVO_CALIBRATION[slave]
        low, high = limits.get(slave, calib.limits or (None, None))  # type: ignore[misc]
        joint = ServoJoint(
            self.transport,
            slave,
            deg_per_count=calib.deg_per_count,
            limits=(low, high) if low is not None else None,
            speed=int(speeds.get(slave, SERVO_DEFAULT_SPEED)),
            requires_prepare=slave in SERVO_REQUIRES_PREPARE,
            zero_counts=calib.zero_counts,
            name=name,
            beeper=beeper,
            logger=self._log,
        )
        joint.identity = IDENTITIES[slave]
        return joint

    # ---- 生命周期 --------------------------------------------------------
    def open(self) -> "RobotArm":
        """打开串口。"""
        self.transport.open()
        return self

    def close(self) -> None:
        self.transport.close()

    def __enter__(self) -> "RobotArm":
        return self.open()

    def __exit__(self, *_exc) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return self.transport.is_open

    @property
    def joints(self) -> List[Tuple[str, object]]:
        """返回 ``[(名称, 关节对象), ...]``。"""
        return [("j1", self.j1), ("j2", self.j2), ("j3", self.j3),
                ("j4", self.j4), ("j5", self.j5), ("j6", self.j6)]

    def __getitem__(self, key: str):
        """``arm["j3"]`` / ``arm["elbow"]`` / ``arm[3]``。"""
        if isinstance(key, int):
            key = "j%d" % key
        name = key.lower()
        aliases = {"base": "j1", "lift": "j2", "elbow": "j3",
                   "forearm": "j4", "wrist": "j5", "gripper": "j6"}
        name = aliases.get(name, name)
        for key_name, joint in self.joints:
            if key_name == name:
                return joint
        raise KeyError("unknown joint %r" % (key,))

    # ---- 批量操作 --------------------------------------------------------
    def verify_all(self, *, raise_on_error: bool = True,
                   tries: Optional[int] = None) -> Dict[str, dict]:
        """校验全部关节的固件身份与故障寄存器。

        :param raise_on_error: True 时第一个失败就抛异常；False 时收集所有结果
        :param tries: 覆盖传输层重试次数（GUI 可用 1 做快速探测）
        """
        results: Dict[str, dict] = {}
        for key, joint in self.joints:
            try:
                words = joint.read_bank(tries=tries)
                joint.verify(words)
                results[key] = {
                    "ok": True,
                    "identity": "id_a=%d build=0x%04X%04X"
                                % (words[0x19], words[0x1B], words[0x1C]),
                    "fault": words[0x17],
                }
            except ArmError as exc:
                results[key] = {"ok": False, "error": str(exc)}
                if raise_on_error:
                    raise
        return results

    def status(self, *, tries: Optional[int] = None) -> Dict[str, dict]:
        """只读快照。**永不抛异常**，断线/挂死的关节返回 ``{"online": False}``。

        :param tries: 覆盖传输层重试次数；GUI 轮询建议用 ``tries=1`` 避免长时间阻塞。
        """
        return {key: joint.status(tries=tries) for key, joint in self.joints}

    def estop(self) -> Dict[str, bool]:
        """紧急停止：对所有关节下发"关闭输出"。

        不先做身份校验（必须在任何时候都能立即执行），逐个独立尝试，
        单个失败不影响其它关节。返回 ``{关节名: 是否成功}``。
        """
        results: Dict[str, bool] = {}
        try:
            self.j1.stop()          # reg0x1F=1（同时撤掉力矩）
            results["j1"] = True
        except ArmError:
            results["j1"] = False
        for key, joint in self.joints[1:]:
            try:
                joint.stop()
                results[key] = True
            except ArmError:
                results[key] = False
        self._log("estop: %s" % results)
        return results

    #: 语义更明确的别名
    stop_all = estop

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "<RobotArm %s open=%s dry_run=%s>" % (
            self.transport.port, self.is_open, self.transport.dry_run
        )


__all__ = ["RobotArm"]
