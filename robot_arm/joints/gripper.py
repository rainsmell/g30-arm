"""关节 6 —— 夹爪（fw2 构建 0xD824，addr 6）。

接口（★ 2026-09-13 修正）::

    reg0x01 R  开口位置**计数**：0 = 闭合，90 = 180° 全开
    reg0x02 R  负载/力矩类反馈（不是位置误差）
    reg0x07 R/W 模式（**上电为 0，必须写 1**）
    reg0x08 R/W 使能（**写 0 = 停 + 失去夹持力**）
    reg0x0A W  速度上限（上电为 0）
    reg0x0B W  **绝对目标位置（计数 0..90）**
    reg0x17 R  故障

早期文档把 ``reg0x01`` 记成"状态码 0/45/90"，那是**采样太粗的误判**：
J6 与 J4/J5 是**同一个固件构建号 ``0xD824``**，``reg0x01`` 就是位置计数，
只是夹爪行程本身只有 **90 个计数**（1 count = 2° 开口），所以看起来只有三个值。

因此 **不需要用"运动时间"推算开度** —— 直接写 ``reg0x0B`` 闭环走到任意开度::

    gripper.open()                 # 全开（闭环，等状态码到位）
    gripper.close()                # 全闭
    gripper.open_for(1.5)          # ★ 张开 1.5 秒后停 -> 中间开度
    gripper.close_for(0.8)         # ★ 闭合 0.8 秒后停
    gripper.stop()                 # reg0x08=0：立停（同时失去夹持力）
    gripper.state()                # 单次短读状态码 / 负载

时序（速度 100）：**全行程约 3.5 s**（其中前 ~0.5 s 状态码就离开 0）。

⚠ **没有夹取检测**：夹住物体与空夹的寄存器读数完全一致
（``reg0x02`` 是负载，不是"夹到了"的判据）。夹持力来自电机扭矩，
``stop()`` 后会被释放。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Optional

from ..exceptions import JointFaultError, MotionTimeoutError, SafetyError
from ..registers import (
    GRIPPER_DEFAULT_SPEED,
    REG_ENABLE,
    REG_FAULT,
    REG_LOOP_ERROR,
    REG_MODE,
    REG_POSITION,
    REG_SPEED_LIMIT,
    REG_TARGET,
)
from ..transport import ModbusTransport
from ..util import Beeper
from .base import BaseJoint

_STATE_COUNT = 0x18

CLOSED = 0
MOVING = 45
OPEN = 90

#: 夹爪全开对应的状态码（0 = 闭合，90 = 180° 全开）
FULL_OPEN_COUNTS = 90
#: 1 个状态码单位对应的开口角度（180° / 90）
DEG_PER_COUNT = 180.0 / FULL_OPEN_COUNTS

#: ★ 从全闭开到全开大约需要的秒数（速度 100 时实测，见 README）
#: 实测：T=0.5~3.0s 状态码为 45（在途中），T>=3.5s 到 90（上限位开关触发）
FULL_TRAVEL_SECONDS = 3.5
#: 从开始运动到状态码离开 “0” 区的耗时（前段基本是空行程/蜜月行程）
DEAD_TIME_SECONDS = 0.5

_NAMES = {CLOSED: "CLOSED", MOVING: "MOVING", OPEN: "OPEN"}


@dataclass
class GripperState:
    """夹爪的一次读数。

    ``code`` = ``reg0x01`` 的**状态码**，只有三个值：

    ======  ==============================
    ``0``   全闭
    ``45``  **在途中**（不是"半开"！）
    ``90``  全开（同时 ``reg0x18`` = 1，上限位开关触发）
    ======  ==============================

    ⚠ **固件不提供连续开度反馈**。精细扫描实测（速度 100）：
    ``T=0.5~3.0s`` 状态码一直是 45，``T>=3.5s`` 才变 90。
    所以中间开度只能用**运行时长**控制（:meth:`GripperJoint.open_for`），
    而"时长 -> 实际间距"必须用卡尺实测（`analysis/gripper_timing_calibration.py`）。
    """

    code: int           # reg0x01：**状态码** 0 / 45 / 90（45 = 在途中）
    err: int            # reg0x02：负载/力矩类反馈（不是位置误差）
    mode: int           # reg0x07
    run: int            # reg0x08
    speed: int          # reg0x0A
    target: int         # reg0x0B
    fault: int          # reg0x17

    #: 别名：``reg0x01`` 数值本身（**不是**连续计数，只有 0/45/90）
    @property
    def counts(self) -> int:
        return self.code

    @property
    def opening_deg(self) -> float:
        """钳口开口角度。

        ⚠ **只有两端有意义**：``code=0`` -> 0°，``code=90`` -> 180°。
        ``code=45`` 表示"在途中"，**具体开度未知**（固件不提供连续反馈）。
        中间开度要用 :meth:`GripperJoint.open_for` 的时长来控制。
        """
        if self.code >= FULL_OPEN_COUNTS:
            return 180.0
        if self.code <= 0:
            return 0.0
        return float("nan")          # 45 = 在途中，开度不可知

    @property
    def opening_ratio(self) -> float:
        """开口比例。同样**只有端点有意义**，在途中返回 ``nan``。"""
        if self.code >= FULL_OPEN_COUNTS:
            return 1.0
        if self.code <= 0:
            return 0.0
        return float("nan")

    @property
    def name(self) -> str:
        if self.code <= 0:
            return _NAMES[CLOSED]
        if self.code >= FULL_OPEN_COUNTS:
            return _NAMES[OPEN]
        return "IN TRANSIT (code=%d)" % self.code

    @property
    def is_closed(self) -> bool:
        return self.code <= 0

    @property
    def is_open(self) -> bool:
        return self.code >= FULL_OPEN_COUNTS

    @property
    def is_moving(self) -> bool:
        return not self.is_closed and not self.is_open

    def as_dict(self) -> dict:
        data = asdict(self)
        data.update(name=self.name, is_open=self.is_open, is_closed=self.is_closed,
                    is_moving=self.is_moving,
                    opening_deg=(None if self.is_moving else self.opening_deg),
                    opening_ratio=(None if self.is_moving else self.opening_ratio),
                    counts=self.code)
        return data

    def __str__(self) -> str:
        return ("code=%d (%s) err=%d mode=%d run=%d speed=%d target=%d fault=%d"
                % (self.code, self.name, self.err, self.mode, self.run,
                   self.speed, self.target, self.fault))


class GripperJoint(BaseJoint):
    """关节 6（夹爪）。"""

    slave = 6
    label = "joint6/gripper"

    def __init__(
        self,
        transport: ModbusTransport,
        *,
        speed: int = GRIPPER_DEFAULT_SPEED,
        name: Optional[str] = None,
        verbose: bool = False,
        beeper: Optional[Beeper] = None,
        logger=None,
    ):
        super().__init__(transport, name=name, verbose=verbose, logger=logger)
        self.speed = speed
        self.beeper = beeper or Beeper(enabled=False)
        self._limits_warned = False

    @property
    def speed(self) -> int:
        """速度上限（写入 ``reg0x0A``，1..732）。直接赋值会被校验。"""
        return self._speed

    @speed.setter
    def speed(self, value: int) -> None:
        value = int(value)
        if not 1 <= value <= 732:
            raise ValueError("gripper speed must be 1..732")
        self._speed = value

    # ---- 读 --------------------------------------------------------------
    def state(self, *, tries: Optional[int] = None) -> GripperState:
        w = self.try_read_words(0x00, _STATE_COUNT, tries=tries)
        if w is None:
            from ..exceptions import TransportError

            raise TransportError("%s: no response from slave 6" % self.name)
        return GripperState(
            code=_s16(w[REG_POSITION]),
            err=_s16(w[REG_LOOP_ERROR]),
            mode=w[REG_MODE],
            run=w[REG_ENABLE],
            speed=w[REG_SPEED_LIMIT],
            target=_s16(w[REG_TARGET]),
            fault=_s16(w[REG_FAULT]),
        )

    # ---- 写 --------------------------------------------------------------
    def prepare(self, *, speed: Optional[int] = None) -> None:
        """``reg0x07=1 -> reg0x0A=速度 -> reg0x08=1``（上电全为 0，不写不动）。"""
        if speed is not None:
            self.speed = speed
        self.write(REG_MODE, 1)
        self.write(REG_SPEED_LIMIT, self.speed)
        self.write(REG_ENABLE, 1)
        if not self.transport.dry_run:
            self.transport.silent_wait(0.3)

    def stop(self) -> None:
        """``reg0x08 = 0``：关闭输出。⚠ 夹爪会**松开/失去保持力**。"""
        self.write(REG_ENABLE, 0)
        self._log("%s: stop (reg0x08=0), grip force released" % self.name)

    def _wait_for(self, want: int, timeout: float, tol: int = 2) -> GripperState:
        """静默等待，然后单次读，直到位置落在 ``want ± tol`` 内。"""
        self.transport.silent_wait(1.0)           # 运动中不轮询
        deadline = time.monotonic() + timeout
        state = self.state()
        while abs(state.code - want) > tol and time.monotonic() < deadline:
            if state.fault:
                raise JointFaultError("%s: fault 0x%04X while moving"
                                      % (self.name, state.fault))
            self.transport.silent_wait(0.35)
            state = self.state()
        return state

    def _goto(self, target: int, label: str, timeout: float,
              tol: int = 2) -> GripperState:
        target = int(target)
        if not 0 <= target <= FULL_OPEN_COUNTS:
            raise SafetyError("gripper target %d out of range 0..%d counts"
                              % (target, FULL_OPEN_COUNTS))
        self.prepare()
        before = self.state()
        self._log("%s: %s (counts %d -> %d, %.0f deg)"
                  % (self.name, label, before.code, target,
                     target * DEG_PER_COUNT))
        self.beeper()
        self.write(REG_TARGET, target)
        if self.transport.dry_run:
            self._log("%s: dry-run - no gripper motion performed" % self.name)
            return self.state()
        state = self._wait_for(target, timeout, tol)
        if abs(state.code - target) > tol:
            raise MotionTimeoutError(
                "%s: %s did not reach code %d within %.1fs (at %d, load=%d)"
                % (self.name, label, target, timeout, state.code, state.err))
        self._log("%s: %s ok (counts=%d, %.0f deg)"
                  % (self.name, label, state.code, state.opening_deg))
        return state

    # ---- 全开 / 全闭（兼容旧接口）--------------------------------
    def open(self, *, timeout: float = 12.0) -> GripperState:
        """完全张开 180°。"""
        return self._goto(FULL_OPEN_COUNTS, "OPEN", timeout)

    def close(self, *, timeout: float = 12.0) -> GripperState:
        """完全闭合。

        ⚠ 无法判断是否真的夹住了物体（无夹取检测）。
        """
        return self._goto(0, "CLOSE", timeout)

    # ---- 任意开度（闭环，不需要用时间推算）------------------------
    def open_to_counts(self, counts: int, *, timeout: float = 12.0,
                       tol: int = 2) -> GripperState:
        """走到指定计数（0 = 闭合，90 = 180° 全开）。"""
        return self._goto(counts, "SET %d counts" % int(counts), timeout, tol)

    def open_to_deg(self, deg: float, *, timeout: float = 12.0,
                    tol: int = 2) -> GripperState:
        """走到指定钳口开口角度（0..180°）。"""
        return self._goto(int(round(float(deg) / DEG_PER_COUNT)),
                          "SET %.0f deg" % float(deg), timeout, tol)

    def open_ratio(self, ratio: float, *, timeout: float = 12.0,
                   tol: int = 2) -> GripperState:
        """按比例张开（0.0 = 闭合，1.0 = 180° 全开）。"""
        ratio = float(ratio)
        if not 0.0 <= ratio <= 1.0:
            raise SafetyError("ratio must be 0..1, got %r" % ratio)
        return self._goto(int(round(ratio * FULL_OPEN_COUNTS)),
                          "SET %.0f%%" % (ratio * 100), timeout, tol)

    # ---- 定时开合（★ 中间开度只能这样控制）----------------------
    def move_for(self, direction: str, seconds: float, *, speed: Optional[int] = None,
                 settle: float = 1.2) -> GripperState:
        """朝一个方向跑 ``seconds`` 秒，然后立即停止（``reg0x08=0``）。

        为什么要这样：实测证明 ``reg0x01`` **只有 0/45/90 三态**
        （45 = 在途中，不是“半开”），**没有连续位置反馈**，所以中间开度
        必须用“跑多久”来控。本方法全程静默，不在运动中读寄存器。

        :param direction: ``"open"`` 或 ``"close"``
        :param seconds: 运行时长（参考：全开约需 3.5 s @ speed 100）
        """
        direction = str(direction).lower()
        if direction not in ("open", "close"):
            raise SafetyError("direction must be 'open' or 'close', got %r" % direction)
        seconds = float(seconds)
        if not 0 < seconds <= 60:
            raise SafetyError("seconds must be in (0, 60], got %r" % seconds)
        self.prepare(speed=speed)
        before = self.state()
        self._log("%s: %s for %.2fs (from code=%d)"
                  % (self.name, direction.upper(), seconds, before.code))
        self.beeper()
        self.write(REG_TARGET, FULL_OPEN_COUNTS if direction == "open" else 0)
        self.transport.silent_wait(seconds)          # 静默运行，不读
        self.write(REG_ENABLE, 0)                    # ★ 停止
        self.transport.silent_wait(settle)
        after = self.state()
        self._log("%s: stopped - code=%d (%s)" % (self.name, after.code, after.name))
        return after

    def open_for(self, seconds: float, **kwargs) -> GripperState:
        """张开 ``seconds`` 秒后停（用于中间开度）。"""
        return self.move_for("open", seconds, **kwargs)

    def close_for(self, seconds: float, **kwargs) -> GripperState:
        """闭合 ``seconds`` 秒后停。"""
        return self.move_for("close", seconds, **kwargs)

    def open_partial(self, ratio: float, *, speed: Optional[int] = None,
                     **kwargs) -> GripperState:
        """按比例开到大致 ``ratio``（用全行程时间线性估算）。

        ⚠ **是估算不是闭环**：``reg0x01`` 无连续位置反馈，建立映射需要拿卡尺
        实测（参 `analysis/gripper_timing_calibration.py`）。
        """
        ratio = float(ratio)
        if not 0.0 <= ratio <= 1.0:
            raise SafetyError("ratio must be 0..1, got %r" % ratio)
        return self.move_for("open", max(0.05, ratio * FULL_TRAVEL_SECONDS),
                             speed=speed, **kwargs)

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


__all__ = ["GripperJoint", "GripperState", "CLOSED", "MOVING", "OPEN",
           "FULL_OPEN_COUNTS", "DEG_PER_COUNT", "FULL_TRAVEL_SECONDS",
           "DEAD_TIME_SECONDS"]
