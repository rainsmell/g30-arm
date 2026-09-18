"""关节基类：共用的读寄存器 / 写寄存器 / 身份校验 / 故障检查。"""

from __future__ import annotations

from typing import Optional

from ..exceptions import IdentityError, JointFaultError, TransportError
from ..protocol import BANK_SIZE, FUNC_WRITE_MULTIPLE
from ..registers import Identity
from ..transport import ModbusTransport
from ..util import Logger, default_logger


class BaseJoint:
    """一个从站通道的抽象基类。

    子类需设置 :attr:`slave`、:attr:`label`、:attr:`identity`，
    并实现 :meth:`stop` 与各自的 :meth:`state`。
    """

    slave: int = 0
    label: str = "joint"
    identity: Optional[Identity] = None

    def __init__(
        self,
        transport: ModbusTransport,
        *,
        name: Optional[str] = None,
        verbose: bool = False,
        logger: Optional[Logger] = None,
    ):
        self.transport = transport
        self.name = name or "%s(addr %d)" % (self.label, self.slave)
        self._log: Logger = logger or default_logger(verbose)

    # ---- 基础 IO ---------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self.transport.is_open

    def read_words(self, start: int, count: int, *, tries: Optional[int] = None) -> tuple[int, ...]:
        """读任意窗口的寄存器。"""
        return self.transport.read_registers(self.slave, start, count, tries=tries)

    def try_read_words(
        self, start: int, count: int, *, tries: Optional[int] = None
    ) -> Optional[tuple[int, ...]]:
        return self.transport.try_read_registers(self.slave, start, count, tries=tries)

    def read_bank(self, *, tries: Optional[int] = None) -> tuple[int, ...]:
        """读完整寄存器页（36 个寄存器）。

        用于身份校验；**不要**在运动过程中调用。
        """
        words = self.try_read_words(0x00, BANK_SIZE, tries=tries)
        if words is None:
            raise TransportError(
                "%s: no response from slave %d - the channel may be hung "
                "(power-cycle the board)" % (self.name, self.slave)
            )
        return words

    def write(self, reg: int, value: int, *, func: int = FUNC_WRITE_MULTIPLE) -> None:
        """写单个寄存器（安全闸门在传输层）。"""
        self.transport.write_register(self.slave, reg, value, func=func)

    # ---- 校验 ------------------------------------------------------------
    def verify(self, words: Optional[tuple[int, ...]] = None, *,
               tries: Optional[int] = None) -> tuple[int, ...]:
        """校验固件身份与故障寄存器。

        :raises IdentityError: 身份不符（**地址映射错了，不要继续下发命令**）
        :raises JointFaultError: ``reg0x17 != 0``
        :raises TransportError: 无应答
        """
        words = self.read_bank(tries=tries) if words is None else words
        if self.identity is not None and not self.identity.matches(words):
            actual = Identity(id_a=words[0x19], build_lo=words[0x1B], build_hi=words[0x1C])
            raise IdentityError(
                "%s: firmware identity mismatch - expected (%s), got (%s). "
                "Check the slave address table before sending any command."
                % (self.name, self.identity, actual)
            )
        fault = words[0x17]
        if fault:
            raise JointFaultError("%s: fault register reg0x17 = 0x%04X" % (self.name, fault))
        return words

    def fault(self, *, tries: Optional[int] = None) -> int:
        """读故障寄存器（0 = 正常）。"""
        return self.read_words(0x17, 1, tries=tries)[0]

    def is_healthy(self, *, tries: Optional[int] = None) -> bool:
        """不抛异常的在线 + 无故障检查。"""
        words = self.try_read_words(0x17, 1, tries=tries)
        return words is not None and words[0] == 0

    # ---- 子类接口 --------------------------------------------------------
    def stop(self) -> None:  # pragma: no cover - 抽象
        """关闭输出 / 停止动作。各子类语义不同，必须显式实现。"""
        raise NotImplementedError

    def status(self, *, tries: Optional[int] = None) -> dict:
        """返回状态字典（不抛异常，供 :meth:`RobotArm.status` 使用）。"""
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "<%s slave=%d>" % (type(self).__name__, self.slave)


__all__ = ["BaseJoint"]
