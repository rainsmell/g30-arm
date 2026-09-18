"""RS485 / Modbus RTU 传输层。

一个 :class:`ModbusTransport` 对应**一条总线**（本机械臂只有一个串口），
所有关节共用同一个传输对象；内部用 ``threading.RLock`` 串行化访问。

本层强制实施的安全策略（**无法通过公有 API 绕过**）::

    * 禁止广播地址 0 与维护地址 0xAA；
    * 禁止写 reg0x05（0xAA11 = 回零到 -2700 / 0xA5A5 = 交替转速测试）；
    * 禁止写 0xAA11/0xAA55 到 reg0x00（系统复位路径）。

另提供 **dry-run 模式**：所有读正常执行，所有写只记录不发送。
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Iterable, Optional, Sequence

from .exceptions import NotConnectedError, ProtocolError, SafetyError, TransportError
from .protocol import (
    FUNC_WRITE_MULTIPLE,
    FUNC_WRITE_SINGLE,
    WRITE_RESPONSE_LEN,
    build_read_holding,
    build_write_multiple,
    build_write_single,
    check_crc,
    expected_read_length,
    parse_read_holding,
    parse_write_response,
)
from .registers import FORBIDDEN_SLAVES, REG_DANGEROUS, REG_VELOCITY
from .util import Logger, default_logger

try:  # pragma: no cover - 环境相关
    import serial
except ImportError:  # pragma: no cover
    serial = None  # type: ignore[assignment]


DEFAULT_PORT = "COM8"
DEFAULT_BAUDRATE = 115200

#: 协议 trace 回调：``hook(direction, data)``，direction 为 ``"tx"`` / ``"rx"``
TraceHook = Callable[[str, bytes], None]

#: 写命令可能的值里，属于"系统复位"魔法字
_RESET_MAGIC = (0xAA11, 0xAA55)


def read_exact(port, length: int, timeout: float) -> bytes:
    """尽力读满 ``length`` 字节；超时返回已读到的部分。"""
    deadline = time.monotonic() + timeout
    buf = bytearray()
    while len(buf) < length and time.monotonic() < deadline:
        chunk = port.read(length - len(buf))
        if chunk:
            buf.extend(chunk)
        else:
            time.sleep(0.005)
    return bytes(buf)


class ModbusTransport:
    """一条 RS485 总线上的 Modbus RTU 主站。"""

    def __init__(
        self,
        port: str = DEFAULT_PORT,
        baudrate: int = DEFAULT_BAUDRATE,
        *,
        timeout: float = 1.0,
        retries: int = 5,
        retry_delay: float = 0.25,
        inter_frame_delay: float = 0.03,
        dry_run: bool = False,
        logger: Optional[Logger] = None,
        verbose: bool = False,
        trace: Optional[TraceHook] = None,
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.retries = retries
        self.retry_delay = retry_delay
        self.inter_frame_delay = inter_frame_delay
        self.dry_run = dry_run
        self._log: Logger = logger or default_logger(verbose)
        #: 可选的原始帧 trace 钩子（GUI 的 debug 面板用）
        self.trace = trace
        self._ser = None
        self._lock = threading.RLock()
        #: dry-run 时累积的"将要发送"的写操作，便于断言/日志
        self.planned_writes: list[tuple[int, int, int]] = []

    # ---- 生命周期 --------------------------------------------------------
    def open(self) -> "ModbusTransport":
        """打开串口（幂等）。"""
        if serial is None:  # pragma: no cover
            raise TransportError("pyserial is not installed: pip install pyserial")
        with self._lock:
            if self._ser is not None:
                return self
            try:
                self._ser = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
            except Exception as exc:  # pragma: no cover - 环境相关
                raise TransportError("cannot open %s: %s" % (self.port, exc)) from exc
            time.sleep(0.1)
            self._ser.reset_input_buffer()
            self._log("opened %s @ %d 8N1" % (self.port, self.baudrate))
            return self

    def close(self) -> None:
        with self._lock:
            if self._ser is not None:
                try:
                    self._ser.close()
                finally:
                    self._ser = None
                    self._log("closed %s" % self.port)

    def __enter__(self) -> "ModbusTransport":
        return self.open()

    def __exit__(self, *_exc) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return self._ser is not None

    def _require_open(self) -> None:
        if self._ser is None:
            raise NotConnectedError("transport is not open; call open() first")

    def silent_wait(self, seconds: float) -> None:
        """静默等待。

        ⚠ 关节运动期间**不要**读取寄存器（会长帧轮询导致从站挂死）。
        需要等待时请调用本方法，而不是自己 sleep + 轮询。
        """
        time.sleep(max(0.0, seconds))

    # ---- 安全闸门 --------------------------------------------------------
    @staticmethod
    def guard_write(slave: int, reg: int, value: int) -> None:
        """写操作前置检查；违规直接抛 :class:`SafetyError`。"""
        if slave in FORBIDDEN_SLAVES:
            raise SafetyError(
                "slave address 0x%02X is not writable (broadcast / maintenance reset)" % slave
            )
        if not 0 <= slave <= 0xFF:
            raise SafetyError("invalid slave address %r" % (slave,))
        if not 0 <= reg <= 0xFFFF:
            raise SafetyError("invalid register 0x%X" % reg)
        if reg == REG_DANGEROUS:
            raise SafetyError(
                "register 0x05 is permanently blocked: 0xAA11 = homing to -2700, "
                "0xA5A5 = alternating speed test"
            )
        if reg == REG_VELOCITY and (value & 0xFFFF) in _RESET_MAGIC:
            raise SafetyError("writing 0x%04X to reg0x00 is blocked (system-reset path)" % value)

    # ---- 底层收发 --------------------------------------------------------
    def _trace(self, direction: str, data: bytes) -> None:
        hook = self.trace
        if hook is None:
            return
        try:
            hook(direction, bytes(data))
        except Exception:
            pass  # trace 永远不能影响控制流

    def _transact(self, frame: bytes, expected_len: int, tries: Optional[int] = None) -> bytes:
        self._require_open()
        attempts = self.retries if tries is None else tries
        last_len = 0
        with self._lock:
            for attempt in range(attempts):
                self._ser.reset_input_buffer()
                self._trace("tx", frame)
                self._ser.write(frame)
                self._ser.flush()
                resp = read_exact(self._ser, expected_len, self.timeout)
                self._trace("rx", resp)
                last_len = len(resp)
                if len(resp) == expected_len:
                    if self.inter_frame_delay:
                        time.sleep(self.inter_frame_delay)
                    return resp
                if attempt + 1 < attempts:
                    time.sleep(self.retry_delay)
        raise TransportError(
            "no valid response after %d attempt(s) on %s: sent %s, got %d/%d bytes; "
            "the slave may be hung (power-cycle the board)"
            % (attempts, self.port, frame.hex(" "), last_len, expected_len)
        )

    # ---- 读 --------------------------------------------------------------
    def read_registers(
        self, slave: int, start: int, count: int, *, tries: Optional[int] = None
    ) -> tuple[int, ...]:
        """读 ``count`` 个保持寄存器（func 0x03）。

        :raises TransportError: 无应答 / 应答长度不足
        :raises ProtocolError: CRC 或帧格式错误
        """
        if slave in FORBIDDEN_SLAVES:
            raise SafetyError("slave address 0x%02X is not readable" % slave)
        frame = build_read_holding(slave, start, count)
        resp = self._transact(frame, expected_read_length(count), tries)
        words = parse_read_holding(resp, count)
        self._log("read slave%d [0x%02X..0x%02X] = %s"
                  % (slave, start, start + count - 1, " ".join("%04X" % w for w in words)))
        return words

    def try_read_registers(
        self, slave: int, start: int, count: int, *, tries: Optional[int] = None
    ) -> Optional[tuple[int, ...]]:
        """读寄存器；失败返回 ``None``（不抛异常）。"""
        try:
            return self.read_registers(slave, start, count, tries=tries)
        except (TransportError, ProtocolError):
            return None

    def read_register(self, slave: int, reg: int, *, tries: Optional[int] = None) -> int:
        """读单个寄存器。"""
        return self.read_registers(slave, reg, 1, tries=tries)[0]

    def probe(self, slave: int, *, tries: int = 1) -> bool:
        """探测某从站是否在线（读一次故障寄存器，不抛异常）。"""
        return self.try_read_registers(slave, 0x17, 1, tries=tries) is not None

    # ---- 写 --------------------------------------------------------------
    def write_register(
        self,
        slave: int,
        reg: int,
        value: int,
        *,
        func: int = FUNC_WRITE_MULTIPLE,
        tries: Optional[int] = None,
    ) -> None:
        """写单个寄存器。

        :param func: ``0x10``（默认，实测最可靠）或 ``0x06``
        :raises SafetyError: 命中安全闸门
        :raises ProtocolError: 回显不匹配
        :raises TransportError: 无应答
        """
        self.guard_write(slave, reg, value)
        value &= 0xFFFF
        if self.dry_run:
            self.planned_writes.append((slave, reg, value))
            self._log("[dry-run] would write slave%d reg0x%02X = 0x%04X" % (slave, reg, value))
            return
        if func == FUNC_WRITE_MULTIPLE:
            frame = build_write_multiple(slave, reg, [value])
        elif func == FUNC_WRITE_SINGLE:
            frame = build_write_single(slave, reg, value)
        else:
            raise ValueError("func must be 0x06 or 0x10")
        resp = self._transact(frame, WRITE_RESPONSE_LEN, tries)
        parse_write_response(resp, frame)
        self._log("write slave%d reg0x%02X = 0x%04X ok" % (slave, reg, value))

    def write_registers(
        self, slave: int, start: int, values: Sequence[int], *, tries: Optional[int] = None
    ) -> None:
        """写多个连续寄存器（func 0x10）。

        ⚠ fw2 每页只有 36 个寄存器，**跨页写会破坏相邻从站**，本方法不检查。
        """
        values = [v & 0xFFFF for v in values]
        for offset, value in enumerate(values):
            self.guard_write(slave, start + offset, value)
        if self.dry_run:
            for offset, value in enumerate(values):
                self.planned_writes.append((slave, start + offset, value))
            self._log("[dry-run] would write slave%d [0x%02X..] = %s"
                      % (slave, start, " ".join("%04X" % v for v in values)))
            return
        frame = build_write_multiple(slave, start, values)
        resp = self._transact(frame, WRITE_RESPONSE_LEN, tries)
        parse_write_response(resp, frame)
        self._log("write slave%d [0x%02X..0x%02X] ok" % (slave, start, start + len(values) - 1))

    # ---- 杂项 ------------------------------------------------------------
    def clear_planned_writes(self) -> list[tuple[int, int, int]]:
        """取出并清空 dry-run 记录的写操作列表。"""
        planned, self.planned_writes = self.planned_writes, []
        return planned

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "<ModbusTransport %s @%d open=%s dry_run=%s>" % (
            self.port, self.baudrate, self.is_open, self.dry_run
        )


__all__ = ["ModbusTransport", "read_exact", "DEFAULT_PORT", "DEFAULT_BAUDRATE", "TraceHook"]
