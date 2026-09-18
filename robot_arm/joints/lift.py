"""关节 2 —— 丝杆升降 / 大臂（fw2，直流 H 桥 + 限位开关）。

接口（★ 已实测）::

    reg0x01 R  限位开关状态编码：0 = 下限位，45 = 中间，90 = 上限位  （**不是角度**）
    reg0x02 R  运动指示：运动 130..155，静止 0
    reg0x07 R  模式 = 0
    reg0x08 R/W 使能（写 0 = 关闭输出）
    reg0x0B W  方向 + 启动：1 = 上行，0 = 下行
    reg0x17 R  故障

⚠⚠ **下行安全约束**：下行运行超过约 3.2 s 会触发固件超速保护
（``0x0800BCC4``: 速度 >= 200.0 持续 400 tick）-> ``0x0800CCF0`` 把 H 桥两路
同时 100% 短接刹车 -> 电流冲击 -> **从站挂死，只能断电**。
因此 :meth:`LiftJoint.down` 强制单次 <= :data:`~robot_arm.registers.LIFT_MAX_PULSE` 秒。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from ..exceptions import MotionTimeoutError, SafetyError
from ..registers import (
    LIFT_MAX_PULSE,
    REG_ENABLE,
    REG_FAULT,
    REG_LOOP_ERROR,
    REG_MODE,
    REG_POSITION,
    REG_TARGET,
    REG_VELOCITY,
    ZONE_LOWER,
    ZONE_MIDDLE,
    ZONE_UPPER,
)
from ..transport import ModbusTransport
from ..util import Beeper
from .base import BaseJoint

_STATE_COUNT = 0x18

UP = 1
DOWN = 0

_ZONE_NAMES = {
    ZONE_LOWER: "LOWER (limit switch pressed, arm near horizontal)",
    ZONE_MIDDLE: "MIDDLE (no limit switch pressed)",
    ZONE_UPPER: "UPPER (limit switch pressed, arm vertical)",
}


@dataclass
class LiftState:
    """关节 2 的一次读数。"""

    zone: int          # reg0x01：0 / 45 / 90
    vel: int           # reg0x00
    motion: int        # reg0x02
    mode: int          # reg0x07
    run: int           # reg0x08
    direction: int     # reg0x0B
    fault: int         # reg0x17

    @property
    def zone_name(self) -> str:
        return _ZONE_NAMES.get(self.zone, "UNKNOWN(%d)" % self.zone)

    @property
    def at_lower_limit(self) -> bool:
        return self.zone == ZONE_LOWER

    @property
    def at_upper_limit(self) -> bool:
        return self.zone == ZONE_UPPER

    @property
    def moving(self) -> bool:
        return self.motion >= 100

    def as_dict(self) -> dict:
        data = asdict(self)
        data.update(zone_name=self.zone_name, moving=self.moving,
                    at_lower_limit=self.at_lower_limit, at_upper_limit=self.at_upper_limit)
        return data

    def __str__(self) -> str:
        return "zone=%d motion=%d dir=%d run=%d mode=%d err=%d fault=%d" % (
            self.zone, self.motion, self.direction, self.run, self.mode, self.vel, self.fault)


class LiftJoint(BaseJoint):
    """关节 2（丝杆升降）。"""

    slave = 2
    label = "joint2/lift"

    def __init__(
        self,
        transport: ModbusTransport,
        *,
        max_pulse: float = LIFT_MAX_PULSE,
        name: Optional[str] = None,
        verbose: bool = False,
        beeper: Optional[Beeper] = None,
        logger=None,
    ):
        super().__init__(transport, name=name, verbose=verbose, logger=logger)
        self.max_pulse = max_pulse
        self.beeper = beeper or Beeper(enabled=False)

    # ---- 读 --------------------------------------------------------------
    def state(self, *, tries: Optional[int] = None) -> LiftState:
        w = self.try_read_words(0x00, _STATE_COUNT, tries=tries)
        if w is None:
            from ..exceptions import TransportError

            raise TransportError(
                "%s: no response from slave 2 - the channel may be hung after an "
                "over-speed fault; power-cycle the board" % self.name
            )
        return LiftState(
            zone=w[REG_POSITION],
            vel=_s16(w[REG_VELOCITY]),
            motion=_s16(w[REG_LOOP_ERROR]),
            mode=w[REG_MODE],
            run=w[REG_ENABLE],
            direction=_s16(w[REG_TARGET]),
            fault=_s16(w[REG_FAULT]),
        )

    # ---- 写 --------------------------------------------------------------
    def enable(self) -> None:
        self.write(REG_ENABLE, 1)

    def stop(self) -> None:
        """``reg0x08 = 0``：关闭 H 桥输出（丝杆自锁，不会自己下滑）。"""
        self.write(REG_ENABLE, 0)
        self._log("%s: stop (reg0x08=0)" % self.name)

    def pulse(self, direction: int, seconds: float) -> LiftState:
        """按方向驱动 ``seconds`` 秒后立即停止。绝不超过 ``max_pulse``。"""
        direction = int(direction)
        if direction not in (UP, DOWN):
            raise SafetyError("direction must be UP(1) or DOWN(0)")
        seconds = float(seconds)
        if not 0 < seconds <= self.max_pulse:
            raise SafetyError(
                "pulse must be in (0, %.2fs]; longer DOWN runs trip the over-speed guard, "
                "apply a hard H-bridge brake and hang the slave" % self.max_pulse
            )
        name = "UP" if direction == UP else "DOWN"
        before = self.state()
        self._log("%s: %s pulse %.2fs from zone=%d"
                  % (self.name, name, seconds, before.zone))
        if not before.run:
            self.enable()
            self.transport.silent_wait(0.3)
        self.beeper()
        self.write(REG_TARGET, direction)
        if self.transport.dry_run:
            self._log("%s: dry-run - no pulse performed" % self.name)
            return self.state()
        self.transport.silent_wait(seconds)       # 静默：运动中不读
        self.write(REG_ENABLE, 0)                 # 脉冲结束立即停
        self.transport.silent_wait(1.2)
        after = self.state()
        self._log("%s: done - zone %d -> %d" % (self.name, before.zone, after.zone))
        return after

    def up(self, seconds: float = 2.0) -> LiftState:
        """上行（安全方向，到上限位会自动停）。"""
        return self.pulse(UP, seconds)

    def down(self, seconds: float = 1.5) -> LiftState:
        """下行脉冲（**受 max_pulse 硬限制**）。"""
        return self.pulse(DOWN, seconds)

    def run_until_limit(
        self, direction: int, *, timeout: float = 12.0, acknowledge_risk: bool = False
    ) -> LiftState:
        """一次跑到底，靠限位开关停止。

        ⚠ 只有当**下限位开关一定会被压下**时才可以用于下行。否则会堵转挂死。
        必须显式传 ``acknowledge_risk=True``。
        """
        direction = int(direction)
        if direction == DOWN and not acknowledge_risk:
            raise SafetyError(
                "run_until_limit(DOWN) requires acknowledge_risk=True: if the lower limit "
                "switch is not actuated the firmware stalls, brakes hard and hangs the slave. "
                "Use down(seconds<=%.1f) instead." % self.max_pulse
            )
        name = "UP" if direction == UP else "DOWN"
        before = self.state()
        self._log("%s: run %s until limit (timeout %.1fs) from zone=%d"
                  % (self.name, name, timeout, before.zone))
        if not before.run:
            self.enable()
            self.transport.silent_wait(0.3)
        self.beeper()
        self.write(REG_TARGET, direction)
        if self.transport.dry_run:
            self._log("%s: dry-run - no run performed" % self.name)
            return self.state()
        self.transport.silent_wait(timeout)
        self.write(REG_ENABLE, 0)
        self.transport.silent_wait(1.0)
        after = self.state()
        wanted = ZONE_UPPER if direction == UP else ZONE_LOWER
        if after.zone != wanted:
            raise MotionTimeoutError(
                "%s: did not reach the %s limit within %.1fs (zone=%d)"
                % (self.name, name, timeout, after.zone)
            )
        self._log("%s: reached %s limit (zone=%d)" % (self.name, name, after.zone))
        return after

    # ---- 报告 ------------------------------------------------------------
    def status(self, *, tries: Optional[int] = None) -> dict:
        try:
            state = self.state(tries=tries)
        except Exception as exc:
            return {"online": False, "error": str(exc)}
        data = state.as_dict()
        data["online"] = True
        return data


def _s16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value >= 0x8000 else value


__all__ = ["LiftJoint", "LiftState", "UP", "DOWN"]
