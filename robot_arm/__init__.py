"""机械臂控制 SDK。

基于对关节固件（``gd32_firmware.bin`` / ``gd32_firmware_2.bin``）的逆向与现场实测，
把已验证的寄存器语义、安全约束和通信时序封装成可直接调用的 Python 接口。

快速开始::

    from robot_arm import RobotArm

    with RobotArm("COM8", verbose=True) as arm:
        print(arm.verify_all())     # 先校验身份（写命令前必须）
        print(arm.status())         # 只读快照
        arm.j3.move_to_deg(45)      # 肘部走到 +45 度
        arm.j5.move_to_deg(90)      # 腕部 roll 到 90 度（垂直）
        arm.j6.close()              # 夹爪闭合
        arm.estop()                 # 关闭所有输出

设计要点
--------

* **安全闸门在传输层**：``reg0x05``（回零 / 交替转速测试）、广播地址、
  ``0xAA`` 维护地址、``reg0x00`` 的复位魔法字 —— 从公有 API **无法**写出。
* **写前校验身份**：``reg0x19/0x1B/0x1C`` 不符直接抛 :class:`IdentityError`。
* **运动期间绝不轮询**：统一"发命令 -> 静默等待 -> 单次读"，
  因为运动中读长帧会让从站挂死（只能断电恢复）。
* **软限位**：位置伺服关节的目标值默认被限制在实测行程内。
* **dry-run**：``RobotArm(dry_run=True)`` 时所有写命令只记录不发送。

权威文档：仓库根目录《机械臂控制规格书》。
"""

from __future__ import annotations

from .arm import RobotArm
from .exceptions import (
    ArmError,
    BlockedError,
    IdentityError,
    JointFaultError,
    MotionTimeoutError,
    NotConnectedError,
    ProtocolError,
    SafetyError,
    TransportError,
)
from .joints import (
    GripperJoint,
    GripperState,
    LiftJoint,
    LiftState,
    ServoJoint,
    ServoState,
    YawJoint,
    YawState,
)
from .protocol import crc16_modbus
from .registers import Calibration, Identity
from .transport import ModbusTransport

__version__ = "1.0.0"

__all__ = [
    "__version__",
    # 门面与传输
    "RobotArm",
    "ModbusTransport",
    # 关节
    "ServoJoint",
    "ServoState",
    "YawJoint",
    "YawState",
    "LiftJoint",
    "LiftState",
    "GripperJoint",
    "GripperState",
    # 数据/工具
    "Identity",
    "Calibration",
    "crc16_modbus",
    # 异常
    "ArmError",
    "TransportError",
    "ProtocolError",
    "IdentityError",
    "JointFaultError",
    "SafetyError",
    "MotionTimeoutError",
    "BlockedError",
    "NotConnectedError",
]
