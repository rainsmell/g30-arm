"""异常类型。

所有异常都继承自 :class:`ArmError`，方便调用方一次捕获。
"""

from __future__ import annotations


class ArmError(Exception):
    """本 SDK 所有异常的基类。"""


class TransportError(ArmError):
    """串口/RS485 层错误：打不开端口、超时无应答、应答长度不对。

    最常见的成因是**从站挂死**（运动中轮询、下行超时硬刹车等），
    此时只能给该板断电重启。
    """


class ProtocolError(ArmError):
    """帧层错误：CRC 不匹配、从站地址不符、功能码异常。"""


class IdentityError(ArmError):
    """固件身份校验失败：目标从站的 ``reg0x19/0x1B/0x1C`` 与预期不符。

    出现本异常说明**地址映射与预期不一致**，绝对不要继续下发动作命令。
    """


class JointFaultError(ArmError):
    """关节 ``reg0x17``（故障寄存器）非 0。"""


class SafetyError(ArmError):
    """被安全策略拦截：危险寄存器、越界目标、超长脉冲等。

    这是**保护性异常**，说明 SDK 主动拒绝了该操作，硬件未受影响。
    """


class MotionTimeoutError(ArmError):
    """运动在给定时间内没有到位。"""


class BlockedError(MotionTimeoutError):
    """关节被堵住：位置环误差 ``reg0x02`` 异常增大（顶到机械限位/被挡住）。"""


class NotConnectedError(ArmError):
    """在未打开串口的情况下调用了需要通信的接口。"""


__all__ = [
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
