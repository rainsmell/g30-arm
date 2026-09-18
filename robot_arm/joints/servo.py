"""位置伺服关节：关节 3 / 4 / 5（fw2，构建 0xD81D / 0xD824）。

接口（★ 已实测）::

    reg0x00 R  当前速度（内部单位）
    reg0x01 R  当前位置（有符号计数）      关节3/4: 1 count = 0.09 deg
                                          关节5  : 1 count = 0.237 deg
    reg0x02 R  负载/力矩类反馈（不是位置误差！静载~10，顶限位~400，
               运动刚结束/保位对抗重力可达 900+；判"卡住"要看位置偏差）
    reg0x07 R/W 模式（1 = 位置模式）
    reg0x08 R/W 使能（写 0 = 关输出、失去保持力矩）
    reg0x0A W  速度上限（1..732）
    reg0x0B W  绝对目标位置 —— 写它即运动
    reg0x17 R  故障

⚠ 关节 5 上电时 mode=0 / enable=0 / speed=0，**写 reg0x0B 不会自动使能**，
所以 :meth:`ServoJoint.prepare` 是必须的（本类会自动调用）。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Optional

from ..exceptions import BlockedError, MotionTimeoutError, SafetyError
from ..registers import (
    REG_ENABLE,
    REG_FAULT,
    REG_LOOP_ERROR,
    REG_MODE,
    REG_POSITION,
    REG_SPEED_LIMIT,
    REG_TARGET,
    REG_VELOCITY,
    SERVO_REQUIRES_PREPARE,
    SERVO_SPEED_MAX,
    STALL_ERROR_THRESHOLD,
)
from ..transport import ModbusTransport
from ..util import Beeper
from .base import BaseJoint

#: 读 state 需要的寄存器数量（0x00..0x17）
_STATE_COUNT = 0x18

#: 速度上限 -> 实际计数/秒 的保守系数（实测 250 -> 约 150 count/s）
_RATE_FACTOR = 0.5


@dataclass
class ServoState:
    """位置伺服关节的一次读数。"""

    vel: int
    pos: int
    #: ``reg0x02``：**负载/力矩类反馈**（fw2 静态分析：filtered current-related
    #: control feedback），**不是**位置误差。
    #:
    #: ⚠ 这只是一个指标，不能单独用来判"卡住"：
    #:   * 静止、驱动关闭时只有个位数；
    #:   * 顶到硬限位约 400；
    #:   * 运动刚结束的冲高、以及保位对抗重力可达 900+。
    #: 所以 :meth:`ServoJoint.move_to_counts` 把**位置偏差**当主判据，
    #: 本字段只作参考。更准确的别名见 :attr:`load`。
    err: int
    mode: int
    run: int
    speed: int
    target: int
    fault: int

    #: 换算比（不属于硬件读数，仅供便利换算）
    deg_per_count: float = 0.09
    #: 物理 0° 对应的原始计数（收拢姿态标定得到）
    zero_counts: int = 0

    @property
    def deg(self) -> float:
        """当前位置（度，相对**收拢姿态标定的 0°**）。"""
        return (self.pos - self.zero_counts) * self.deg_per_count

    @property
    def target_deg(self) -> float:
        return (self.target - self.zero_counts) * self.deg_per_count

    @property
    def blocked(self) -> bool:
        """负载指标（``reg0x02``）偏高。

        ⚠ 这**只是一个指标**，不是"卡住"的结论：

        * 运动刚结束时会冲到 800+，而此时位置往往已经到位；
        * 静止、驱动关闭时只有个位数；
        * 顶到硬限位或用手强扭时才会持续偏高。

        是否"真的卡住"要看**位置偏差**，见 :meth:`ServoJoint.move_to_counts`。
        """
        return abs(self.err) > STALL_ERROR_THRESHOLD

    @property
    def load(self) -> int:
        """``reg0x02`` 的语义更准确的别名（负载/力矩类反馈，不是位置误差）。"""
        return self.err

    @property
    def enabled(self) -> bool:
        return bool(self.run)

    def as_dict(self) -> dict:
        data = asdict(self)
        data.update(deg=self.deg, blocked=self.blocked, enabled=self.enabled,
                    load=self.load)
        return data

    def __str__(self) -> str:
        return (
            "pos=%d (%.2f deg) target=%d vel=%d load=%d mode=%d run=%d speed=%d fault=%d"
            % (self.pos, self.deg, self.target, self.vel, self.err,
               self.mode, self.run, self.speed, self.fault)
        )


class ServoJoint(BaseJoint):
    """位置伺服关节（3 / 4 / 5）。"""

    label = "servo"

    def __init__(
        self,
        transport: ModbusTransport,
        slave: int,
        *,
        deg_per_count: float,
        limits: Optional[tuple[int, int]] = None,
        speed: int = 300,
        requires_prepare: bool = False,
        zero_counts: int = 0,
        name: Optional[str] = None,
        verbose: bool = False,
        beeper: Optional[Beeper] = None,
        logger=None,
    ):
        super().__init__(transport, name=name, verbose=verbose, logger=logger)
        self.slave = slave
        self.deg_per_count = deg_per_count
        self.limits = limits
        self.zero_counts = int(zero_counts)
        self._speed = self._check_speed(speed)
        self.requires_prepare = requires_prepare or slave in SERVO_REQUIRES_PREPARE
        self.beeper = beeper or Beeper(enabled=False)
        self._limits_warned = False

    # ---- 基本校验 --------------------------------------------------------
    @staticmethod
    def _check_speed(speed: int) -> int:
        speed = int(speed)
        if not 1 <= speed <= SERVO_SPEED_MAX:
            raise ValueError("speed must be 1..%d" % SERVO_SPEED_MAX)
        return speed

    @property
    def speed(self) -> int:
        """速度上限（写入 ``reg0x0A`` 的值，1..732）。直接赋值会被校验。"""
        return self._speed

    @speed.setter
    def speed(self, value: int) -> None:
        self._speed = self._check_speed(value)

    def set_limits(self, low: int, high: int) -> None:
        """设置软限位（计数，相对上电零点）。"""
        if low >= high:
            raise ValueError("low must be < high")
        self.limits = (int(low), int(high))

    # ---- 读写 ------------------------------------------------------------
    def state(self, *, tries: Optional[int] = None) -> ServoState:
        """单次读取关节状态（运动期间不要调用）。"""
        w = self.try_read_words(0x00, _STATE_COUNT, tries=tries)
        if w is None:
            from ..exceptions import TransportError

            raise TransportError("%s: no response from slave %d" % (self.name, self.slave))
        return ServoState(
            vel=_s16(w[REG_VELOCITY]),
            pos=_s16(w[REG_POSITION]),
            err=_s16(w[REG_LOOP_ERROR]),
            mode=w[REG_MODE],
            run=w[REG_ENABLE],
            speed=w[REG_SPEED_LIMIT],
            target=_s16(w[REG_TARGET]),
            fault=_s16(w[REG_FAULT]),
            deg_per_count=self.deg_per_count,
            zero_counts=self.zero_counts,
        )

    def prepare(self) -> None:
        """设置模式 + 速度上限 + 使能。关节 5 必须调用，其余关节也可安全调用。"""
        if not self.transport.dry_run:
            current_mode = self.try_read_words(REG_MODE, 1)
            if current_mode is not None and current_mode[0] != 1:
                self.write(REG_MODE, 1)
                time.sleep(0.1)
        self.write(REG_SPEED_LIMIT, self.speed)
        self.write(REG_ENABLE, 1)
        if not self.transport.dry_run:
            time.sleep(0.3)

    def set_speed(self, speed: int) -> None:
        """设置速度上限并立即下发。"""
        self.speed = self._check_speed(speed)
        self.write(REG_SPEED_LIMIT, self.speed)

    def enable(self) -> None:
        self.write(REG_ENABLE, 1)

    def stop(self) -> None:
        """写 ``reg0x08 = 0``：固件清 CCER、三路 PWM 关闭。

        ⚠ **会失去保持力矩**，重力负载会下垂。停止前先把机械臂支撑好。
        """
        self.write(REG_ENABLE, 0)
        self._log("%s: stop (reg0x08=0), holding torque removed" % self.name)

    # 别名，语义更直观
    disable = stop

    # ---- 运动 ------------------------------------------------------------
    def _check_target(self, counts: int) -> int:
        counts = int(counts)
        if not -32768 <= counts <= 32767:
            raise SafetyError("target %d does not fit in int16" % counts)
        if self.limits is not None:
            low, high = self.limits
            if not low <= counts <= high:
                raise SafetyError(
                    "target %d (%.2f deg) outside soft limits [%d, %d] (%.2f..%.2f deg). "
                    "Widen only after re-measuring the travel from the power-on position."
                    % (counts, self.counts_to_deg(counts), low, high,
                       self.counts_to_deg(low), self.counts_to_deg(high))
                )
        elif not self._limits_warned:
            self._limits_warned = True
            self._log("%s: WARNING - no soft limits configured" % self.name)
        return counts

    def move_to_counts(
        self,
        counts: int,
        *,
        settle_tol: int = 10,
        timeout: float = 30.0,
        raise_on_block: bool = True,
    ) -> ServoState:
        """移动到绝对计数位置。

        流程严格遵循"发命令 -> 静默等待 -> 单次读"：运动中永不轮询。
        """
        counts = self._check_target(counts)
        current = self.state()
        self.prepare()
        distance = abs(counts - current.pos)
        wait = distance / max(1.0, _RATE_FACTOR * self.speed) + 0.9
        self._log("%s: move %d -> %d (%+.2f deg), estimated %.1fs"
                  % (self.name, current.pos, counts,
                     (counts - current.pos) * self.deg_per_count, wait))
        self.beeper()
        self.write(REG_TARGET, counts)
        if self.transport.dry_run:
            self._log("%s: dry-run - no motion performed" % self.name)
            return self.state()
        self.transport.silent_wait(wait)          # 静默：运动期间不读

        deadline = time.monotonic() + timeout
        state = self.state()
        while abs(state.pos - counts) > settle_tol and time.monotonic() < deadline:
            self.transport.silent_wait(0.35)
            state = self.state()

        short = state.pos - counts
        off_target = abs(short) > settle_tol
        # ★ 主判据是**位置偏差**，reg0x02 只作参考。
        #   reg0x02 是动态力矩/电流类反馈，运动刚结束时能冲到 800+，
        #   而位置其实已经到位 —— 以前只凭它判"堆转"会误报。
        if off_target and state.blocked:
            self._log("%s: BLOCKED - loop error reg0x02=%d, short by %d counts"
                      % (self.name, state.err, short))
            if raise_on_block:
                raise BlockedError(
                    "%s: blocked - stopped at pos=%d (target %d, short by %d, "
                    "loop error reg0x02=%d)"
                    % (self.name, state.pos, counts, short, state.err)
                )
        elif off_target:
            self._log("%s: settled %d counts short of target" % (self.name, short))
            if raise_on_block:
                raise MotionTimeoutError(
                    "%s: did not reach target %d (stopped at %d, short by %d)"
                    % (self.name, counts, state.pos, short)
                )
        elif state.blocked:
            # 已到位，但力矩指标偏高：仅记录，不报错
            self._log("%s: reached target; note - loop error reg0x02=%d (运动刚结束的冲高)"
                      % (self.name, state.err))
        self._log("%s: done - %s" % (self.name, state))
        return state

    def move_to_deg(self, deg: float, **kwargs) -> ServoState:
        """移动到绝对角度（相对**上电零点**）。"""
        return self.move_to_counts(self.deg_to_counts(deg), **kwargs)

    def move_rel_counts(self, delta: int, **kwargs) -> ServoState:
        """相对当前位置移动若干个计数。"""
        return self.move_to_counts(self.state().pos + int(delta), **kwargs)

    def move_rel_deg(self, delta: float, **kwargs) -> ServoState:
        """相对当前位置移动若干度。"""
        return self.move_to_counts(self.state().pos + self.deg_to_counts(delta), **kwargs)

    # ---- 换算 ------------------------------------------------------------
    def counts_to_deg(self, counts: float) -> float:
        return (counts - self.zero_counts) * self.deg_per_count

    def deg_to_counts(self, deg: float) -> int:
        return int(round(deg / self.deg_per_count)) + self.zero_counts

    # ---- 报告 ------------------------------------------------------------
    def status(self, *, tries: Optional[int] = None) -> dict:
        try:
            state = self.state(tries=tries)
        except Exception as exc:
            return {"online": False, "error": str(exc)}
        data = state.as_dict()
        data["online"] = True
        if self.limits:
            data["limits"] = list(self.limits)
            data["limits_deg"] = [self.counts_to_deg(self.limits[0]),
                                  self.counts_to_deg(self.limits[1])]
        return data


def _s16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value >= 0x8000 else value


__all__ = ["ServoJoint", "ServoState"]
