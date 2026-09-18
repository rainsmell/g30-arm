"""关节 1 —— 底座回转（fw1，单通道直流 + 限位）。

接口（★ 已实测）::

    reg0x01 R  位置分区 0 / 45 / 90     1 单位 ~ 2 物理度
    reg0x02 R  模拟量/原始输入（空闲 1，运动 0；手转关节时 0..11 抖动）
    reg0x0B W  方向 + 启动：0xAA11 = A，0x0000 = B
    reg0x09 W  写 0x1234 清除锁定
    reg0x17 R  故障
    reg0x18 R  去抖后的限位状态
    reg0x1F W  PWM 幅值覆盖：1 = 抑制（**并撤掉力矩**），0x1482 = 常规速度

⚠ 本关节**没有位置闭环**：``reg0x01`` 只有三档，定位靠"方向 + 脉冲时长"。
⚠ 停止（``reg0x1F=1``）会**撤掉力矩**，关节变自由可反驱。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from ..exceptions import SafetyError
from ..registers import (
    J1_DIRECTION_A,
    J1_DIRECTION_B,
    J1_OVERRIDE_FAST,
    J1_OVERRIDE_INHIBIT,
    J1_OVERRIDE_RUN,
    J1_UNLOCK_VALUE,
    J1_ZONE_DEG_PER_UNIT,
    REG_FAULT,
)
from ..transport import ModbusTransport
from ..util import Beeper
from .base import BaseJoint

_STATE_COUNT = 0x20            # 0x00..0x1F

#: 方向常量
DIRECTION_A = J1_DIRECTION_A   # ★ 实测 reg0x01 分区 45 -> 90
DIRECTION_B = J1_DIRECTION_B   # ★ 实测 reg0x01 分区 45 -> 0

_DIRECTIONS = {
    "a": DIRECTION_A,
    "b": DIRECTION_B,
    "cw": DIRECTION_B,
    "ccw": DIRECTION_A,
    "up": DIRECTION_A,
    "down": DIRECTION_B,
}


@dataclass
class YawState:
    """关节 1 的一次读数。"""

    zone: int              # reg0x01：0 / 45 / 90
    analog: int            # reg0x02
    direction: int         # reg0x0B
    fault: int             # reg0x17
    limit: int             # reg0x18
    speed_override: int    # reg0x1F
    deg_per_zone_unit: float = J1_ZONE_DEG_PER_UNIT

    @property
    def deg(self) -> float:
        """由分区值换算的**粗**角度（分辨率约 90 度）。"""
        return self.zone * self.deg_per_zone_unit

    @property
    def moving(self) -> bool:
        return self.analog == 0

    @property
    def inhibited(self) -> bool:
        return self.speed_override == J1_OVERRIDE_INHIBIT

    def as_dict(self) -> dict:
        data = asdict(self)
        data.update(deg=self.deg, moving=self.moving, inhibited=self.inhibited)
        return data

    def __str__(self) -> str:
        return (
            "zone=%d (~%.0f deg) analog=%d dir=0x%04X limit=%d override=0x%04X fault=%d"
            % (self.zone, self.deg, self.analog, self.direction, self.limit,
               self.speed_override, self.fault)
        )


class YawJoint(BaseJoint):
    """关节 1（底座回转，fw1）。"""

    slave = 1
    label = "joint1/yaw"

    #: 单次脉冲的默认时长（秒）；2.5 s 约 90 度
    DEFAULT_PULSE = 2.5

    def __init__(
        self,
        transport: ModbusTransport,
        *,
        speed: int = J1_OVERRIDE_RUN,
        max_pulse: float = 5.0,
        name: Optional[str] = None,
        verbose: bool = False,
        beeper: Optional[Beeper] = None,
        logger=None,
    ):
        super().__init__(transport, name=name, verbose=verbose, logger=logger)
        self.speed = speed
        self.max_pulse = max_pulse
        self.beeper = beeper or Beeper(enabled=False)

    # ---- 读 --------------------------------------------------------------
    def state(self, *, tries: Optional[int] = None) -> YawState:
        w = self.try_read_words(0x00, _STATE_COUNT, tries=tries)
        if w is None:
            from ..exceptions import TransportError

            raise TransportError("%s: no response from slave 1" % self.name)
        return YawState(
            zone=w[0x01],
            analog=w[0x02],
            direction=w[0x0B],
            fault=_s16(w[REG_FAULT]),
            limit=w[0x18],
            speed_override=w[0x1F],
        )

    # ---- 写 --------------------------------------------------------------
    def set_speed_override(self, value: int) -> None:
        """写 ``reg0x1F``。只建议使用 1 / 0x1482 / 0x36B0 这三个已验证值。"""
        if value not in (J1_OVERRIDE_INHIBIT, J1_OVERRIDE_RUN, J1_OVERRIDE_FAST):
            self._log("%s: WARNING - override 0x%04X is not one of the verified values"
                      % (self.name, value))
        self.write(0x1F, value)
        self.speed = value

    def start(self, direction, *, speed: Optional[int] = None) -> None:
        """设定速度并写入方向 —— 电机立即开始转。"""
        self.set_speed_override(self.speed if speed is None else speed)
        self.transport.silent_wait(0.3)
        self.write(0x0B, _resolve_direction(direction))

    def stop(self) -> None:
        """``reg0x1F = 1``：抑制输出，**同时撤掉力矩**。"""
        self.write(0x1F, J1_OVERRIDE_INHIBIT)
        self.speed = J1_OVERRIDE_INHIBIT
        self._log("%s: stop (reg0x1F=1), holding torque removed" % self.name)

    def clear_fault(self) -> None:
        """``reg0x09 = 0x1234``：清除锁定/错误状态。"""
        self.write(0x09, J1_UNLOCK_VALUE)

    def rotate(
        self,
        direction,
        seconds: float = DEFAULT_PULSE,
        *,
        speed: Optional[int] = None,
        hard_limit: bool = True,
    ) -> YawState:
        """按方向转动 ``seconds`` 秒后停止。

        :param direction: ``"a"``/``"b"``/``"cw"``/``"ccw"``/``"up"``/``"down"``
                          或 :data:`DIRECTION_A` / :data:`DIRECTION_B`
        :param hard_limit: 是否强制 ``seconds <= max_pulse``
        """
        seconds = float(seconds)
        if hard_limit and seconds > self.max_pulse:
            raise SafetyError(
                "%.2fs pulse exceeds max_pulse=%.2fs; the joint has no position feedback "
                "so long open-loop runs are unsafe" % (seconds, self.max_pulse)
            )
        if seconds <= 0:
            raise SafetyError("seconds must be > 0")
        value = _resolve_direction(direction)
        before = self.state()
        self._log("%s: rotate dir=0x%04X for %.2fs from zone=%d"
                  % (self.name, value, seconds, before.zone))
        self.set_speed_override(self.speed if speed is None else speed)
        self.transport.silent_wait(0.3)
        self.beeper()
        self.write(0x0B, value)
        if self.transport.dry_run:
            self._log("%s: dry-run - no rotation performed" % self.name)
            return self.state()
        self.transport.silent_wait(seconds)       # 静默：运动中不读
        self.write(0x1F, J1_OVERRIDE_INHIBIT)
        self.transport.silent_wait(1.2)
        after = self.state()
        self._log("%s: done - zone %d -> %d" % (self.name, before.zone, after.zone))
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


def _resolve_direction(direction) -> int:
    if isinstance(direction, str):
        key = direction.lower()
        if key not in _DIRECTIONS:
            raise ValueError("direction must be one of %s" % sorted(_DIRECTIONS))
        return _DIRECTIONS[key]
    value = int(direction) & 0xFFFF
    if value not in (DIRECTION_A, DIRECTION_B):
        raise ValueError("direction must be 0xAA11 (A) or 0x0000 (B)")
    return value


def _s16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value >= 0x8000 else value


__all__ = ["YawJoint", "YawState", "DIRECTION_A", "DIRECTION_B"]
