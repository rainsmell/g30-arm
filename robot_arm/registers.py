"""寄存器地址、固件身份表与出厂标定数据。

所有数值的来源见仓库根目录的《机械臂控制规格书》。
置信度：★ 已实测 / ◇ 静态分析 / ○ 推断。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# --------------------------------------------------------------------------
# 通用寄存器（fw2 关节 2/3/4/5/6 的语义基本一致）
# --------------------------------------------------------------------------
REG_VELOCITY = 0x00        # R  当前速度（内部单位）
REG_POSITION = 0x01        # R  位置（伺服=计数；关节 2/6=状态分区）
REG_LOOP_ERROR = 0x02      # R  ★负载/力矩类反馈（reg0x02）——
                           #    不是位置误差！静载~10 / 顶限位~400 / 保位对抗重力~900+
REG_LOOP_ERROR_MIRROR = 0x04  # R  同 0x02 的镜像
REG_DANGEROUS = 0x05       # W  ★禁止：0xAA11=回零到 -2700，0xA5A5=交替转速测试
REG_MODE = 0x07            # R/W 模式（1=位置模式）
REG_ENABLE = 0x08          # R/W 使能（写 0 = 关输出、失去保持力矩）
REG_START = 0x09           # W  启动命令 0x1234（本项目未使用）
REG_SPEED_LIMIT = 0x0A     # W  速度上限（伺服关节，最大 732）
REG_TARGET = 0x0B          # W  伺服=绝对目标位置；关节2=方向；关节1=方向
REG_FAULT = 0x17           # R  故障（0 = 正常）
REG_LIMIT_INPUT = 0x18     # R  限位输入
REG_ID_A = 0x19            # R  身份字 A
REG_ID_B = 0x1A            # R  身份字 B（随构建变化）
REG_BUILD_LO = 0x1B        # R  ★身份主判定字（低）
REG_BUILD_HI = 0x1C        # R  ★身份主判定字（高）
REG_PARAM_13 = 0x13        # R  参数（各关节不同：180/700/1500/550）
REG_PARAM_14 = 0x14        # R  参数（各关节不同：1000/400/1000/100）

# --------------------------------------------------------------------------
# fw1（关节 1）专有
# --------------------------------------------------------------------------
REG1_POSITION_ZONE = 0x01     # R  位置分区：0 / 45 / 90
REG1_ANALOG = 0x02            # R  模拟量/原始输入位域
REG1_UNLOCK = 0x09            # W  写 0x1234 清除锁定/错误
REG1_DIRECTION = 0x0B         # W  方向+启动：0xAA11 / 0x0000
REG1_LIMIT = 0x18             # R  去抖后的限位状态
REG1_SPEED_OVERRIDE = 0x1F    # W  PWM 幅值覆盖

# 关节 1 的 speed override 取值（★已实测）
J1_OVERRIDE_INHIBIT = 0x0001   # 抑制/停 —— 同时撤掉力矩
J1_OVERRIDE_RUN = 0x1482       # 常规工作速度
J1_OVERRIDE_FAST = 0x36B0      # 高速（◇ 静态推断，慎用）

J1_DIRECTION_A = 0xAA11        # ★ 实测 reg0x01 分区 45 -> 90
J1_DIRECTION_B = 0x0000        # ★ 实测 reg0x01 分区 45 -> 0
J1_UNLOCK_VALUE = 0x1234

# --------------------------------------------------------------------------
# 身份与标定
# --------------------------------------------------------------------------

#: 禁止访问的从站地址（广播 / 维护地址）
FORBIDDEN_SLAVES = (0x00, 0xAA)


@dataclass(frozen=True)
class Identity:
    """固件身份：``reg0x19`` / ``reg0x1B`` / ``reg0x1C``。"""

    id_a: int
    build_lo: int
    build_hi: int

    def matches(self, words) -> bool:
        return (
            (words[REG_ID_A] & 0xFFFF) == self.id_a
            and (words[REG_BUILD_LO] & 0xFFFF) == self.build_lo
            and (words[REG_BUILD_HI] & 0xFFFF) == self.build_hi
        )

    def __str__(self) -> str:
        return "id_a=%d build=0x%04X%04X" % (self.id_a, self.build_lo, self.build_hi)


@dataclass(frozen=True)
class Calibration:
    """位置换算与软限位。

    :param deg_per_count: 每计数对应的物理角度
    :param limits: 软限位（**原始计数**，含零位偏移）
    :param zero_counts: 物理 0° 对应的原始计数（收拢姿态标定得到）

    ``limits`` 用原始计数是为了让限位直接对应寄存器值，
    不受换算率误差影响 —— 换算率错了只是“度”不准，不会让机构撞死点。
    """

    deg_per_count: float
    limits: Optional[Tuple[int, int]] = None
    zone_deg_per_unit: Optional[float] = None   # 只有分区反馈的关节才有
    zero_counts: int = 0

    def counts_to_deg(self, counts: float) -> float:
        return (counts - self.zero_counts) * self.deg_per_count

    def deg_to_counts(self, deg: float) -> int:
        return int(round(deg / self.deg_per_count)) + self.zero_counts


#: 每个从站的固件身份（★ 已实测）
IDENTITIES: Dict[int, Identity] = {
    1: Identity(id_a=1, build_lo=0x67C6, build_hi=0xD7FB),   # fw1：直流 + 限位
    2: Identity(id_a=2, build_lo=0x67C6, build_hi=0xD81D),   # fw2 通道：丝杆方向型
    3: Identity(id_a=2, build_lo=0x67C6, build_hi=0xD81D),   # fw2 通道：位置伺服
    4: Identity(id_a=2, build_lo=0x67C6, build_hi=0xD824),   # 位置伺服
    5: Identity(id_a=2, build_lo=0x67C6, build_hi=0xD824),   # 腕部 roll（上电未使能）
    6: Identity(id_a=2, build_lo=0x67C6, build_hi=0xD824),   # 夹爪
}

#: 位置伺服关节的换算（★ 已实测）
#:
#: ⚠ 2026-09-13 现场标定，两件事都改了：
#:
#: **1) 零点 = 完全收拢姿态**（上电即收拢，行程 **单向 [0, +N]**）
#:    旧值按“上电在行程中心”估的对称限位，在现在这个零点下
#:    负值方向直接就是机械死点。
#:
#: **2) 换算率从 0.09 修正为 0.1009 deg/count** —— 用 0°->180° 两点实测：
#:       收拢 0° = 1 count；转到 180° 后 = 1785 count
#:       (1785-1)/180 = 9.9111 count/deg = 0.10090 deg/count
#:    旧假设偏小 11%，意味着旧值下 `move_to_deg(250)` 实际会走到 280°，
#:    **超过了 270° 的底座干涉线** —— 所以限位也一并按新率重算。
#:
#:    注：用户目测的 180° 有误差；若真实率是整十的 0.1，
#:    差异只有 1%，不影响安全。用 `analysis/encoder_calibration.py`
#:    再补 90°/250° 两个点可以进一步收窄。
SERVO_CALIBRATION: Dict[int, Calibration] = {
    # J3：上限 250°（用户指定；超过 ~270° 与底座干涉）
    #     取 2479 是为了让 `move_to_deg(250.0)` 刚好能过（= 250.02°）
    3: Calibration(deg_per_count=0.100897, limits=(1, 2479), zero_counts=1),
    # J4：2026-09-13 现场标定。rev=4 三个点（零点 1 count）：
    #     45° -> +459  90° -> +875   最小二乘拟合 -> 9.8178 count/deg
    #     即 0.101856 deg/count。
    #
    #   为什么不用“仅 90°”的 9.7222：单点目测误差太大。三点看下来
    #   离散（10.20 / 9.72 / 9.91）比“J4 真实差异”的预期大得多，
    #   说明主要是目测误差。用 J3 的率（9.9111）反推用户的目测：
    #   “90°”实际 88.3°、“45°”实际 46.3° —— 两个 ±1.7° 以内的反向误差，
    #   完全在手眼标定误差量级。
    #
    #   剩余不确定度 ~1%：要再精确必须有外部角度基准（或假定 J4 与 J3
    #   同款减速箱、直接用 0.100897 —— 那是假设不是实测）。
    #   上限 1768 -> (1768-1)*0.101856 = 179.98°
    #   （用户 2026-09-13 实测：J3 先摆到 15°，J4 就能到 180° 不干涉）
    4: Calibration(deg_per_count=0.101856, limits=(1, 1768), zero_counts=1),
    # J5：尚未标定，保留原值
    5: Calibration(deg_per_count=0.237, limits=(0, 681)),
}

#: 关节 3/4 固件在写 reg0x0B 时会自动使能；关节 5 必须显式 prepare()
SERVO_REQUIRES_PREPARE = (5,)

#: 伺服关节速度上限
SERVO_SPEED_MAX = 732
SERVO_DEFAULT_SPEED = 300

#: 关节 5 的出厂软限位窗口（固件钳位区间，★ 已实测）
J5_FIRMWARE_WINDOW = (0, 681)

#: 位置环误差超过该值判定为"堵转/被挡"
#: reg0x02 超过该值算"负载偏高"（只是提示；真正卡住要同时看位置偏差）
STALL_ERROR_THRESHOLD = 500

#: 关节 2 单次脉冲上限（秒）。超过 ~3.2 s 会触发固件超速保护 → 硬刹车 → 从站挂死
LIFT_MAX_PULSE = 2.5

#: 关节 2/6 的状态分区编码
ZONE_LOWER = 0
ZONE_MIDDLE = 45
ZONE_UPPER = 90

#: 夹爪目标
GRIPPER_TARGET_OPEN = 90
GRIPPER_TARGET_CLOSE = 0
GRIPPER_DEFAULT_SPEED = 100

#: 关节 1 分区值 -> 物理角度（1 单位 = 2 度，★ 已实测）
J1_ZONE_DEG_PER_UNIT = 2.0

__all__ = [name for name in dir() if name.isupper()] + [
    "Identity",
    "Calibration",
]
