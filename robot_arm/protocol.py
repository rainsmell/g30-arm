"""Modbus RTU 帧的构造与解析。

线上格式（寄存器内容大端，CRC 低字节在前）::

    请求   读保持   : [slave][0x03][startHi][startLo][countHi][countLo]        [crcLo][crcHi]
    响应   读保持   : [slave][0x03][byteCount][data...]                        [crcLo][crcHi]
    请求   写单     : [slave][0x06][regHi][regLo][valHi][valLo]                [crcLo][crcHi]
    请求   写多     : [slave][0x10][startHi][startLo][qtyHi][qtyLo][cnt][...]  [crcLo][crcHi]
    响应   写单/写多: 原样回显请求帧
"""

from __future__ import annotations

from typing import Iterable, Sequence

from .exceptions import ProtocolError

FUNC_READ_HOLDING = 0x03
FUNC_WRITE_SINGLE = 0x06
FUNC_WRITE_MULTIPLE = 0x10

CRC_SEED = 0xFFFF
CRC_POLY = 0xA001

#: fw2 每个从站的寄存器页容量（0x00..0x23）
BANK_SIZE = 0x24

#: sizeof 写单/写多的应答帧
WRITE_RESPONSE_LEN = 8


def crc16_modbus(data: bytes) -> int:
    """标准 Modbus CRC16（多项式 0xA001，初值 0xFFFF）。"""
    crc = CRC_SEED
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ CRC_POLY
            else:
                crc >>= 1
    return crc & 0xFFFF


def append_crc(body: bytes) -> bytes:
    """在帧体后追加 CRC（低字节在前）。"""
    crc = crc16_modbus(body)
    return body + bytes((crc & 0xFF, (crc >> 8) & 0xFF))


def check_crc(frame: bytes) -> bool:
    """校验整帧（含 CRC）的 CRC 是否正确。"""
    if len(frame) < 4:
        return False
    return crc16_modbus(frame[:-2]) == int.from_bytes(frame[-2:], "little")


def to_u16(value: int) -> int:
    """把任意整数折成 16 位无符号。"""
    return int(value) & 0xFFFF


def s16(value: int) -> int:
    """把读到的 16 位寄存器解释为有符号数。"""
    value &= 0xFFFF
    return value - 0x10000 if value >= 0x8000 else value


def build_read_holding(slave: int, start: int, count: int) -> bytes:
    """构造读保持寄存器请求（func 0x03）。"""
    if not 1 <= count <= 0x7D:
        raise ValueError("count must be 1..125")
    body = bytes((
        slave & 0xFF,
        FUNC_READ_HOLDING,
        (start >> 8) & 0xFF, start & 0xFF,
        (count >> 8) & 0xFF, count & 0xFF,
    ))
    return append_crc(body)


def build_write_single(slave: int, reg: int, value: int) -> bytes:
    """构造写单寄存器请求（func 0x06）。"""
    body = bytes((
        slave & 0xFF,
        FUNC_WRITE_SINGLE,
        (reg >> 8) & 0xFF, reg & 0xFF,
        (to_u16(value) >> 8) & 0xFF, to_u16(value) & 0xFF,
    ))
    return append_crc(body)


def build_write_multiple(slave: int, start: int, values: Sequence[int]) -> bytes:
    """构造写多寄存器请求（func 0x10）。"""
    values = list(values)
    if not 1 <= len(values) <= 0x7B:
        raise ValueError("values must contain 1..123 registers")
    body = bytearray((
        slave & 0xFF,
        FUNC_WRITE_MULTIPLE,
        (start >> 8) & 0xFF, start & 0xFF,
        (len(values) >> 8) & 0xFF, len(values) & 0xFF,
        (len(values) * 2) & 0xFF,
    ))
    for value in values:
        value = to_u16(value)
        body.append((value >> 8) & 0xFF)
        body.append(value & 0xFF)
    return append_crc(bytes(body))


def expected_read_length(count: int) -> int:
    """func 0x03 应答帧的总长度（含 CRC）。"""
    return 3 + count * 2 + 2


def parse_read_holding(frame: bytes, count: int) -> tuple[int, ...]:
    """把 func 0x03 的应答帧解析成寄存器元组。

    :raises ProtocolError: 长度、CRC、功能码或字节数不合法
    """
    need = expected_read_length(count)
    if len(frame) != need:
        raise ProtocolError("short read response: got %d bytes, expected %d" % (len(frame), need))
    if not check_crc(frame):
        raise ProtocolError("CRC mismatch on read response: %s" % frame.hex(" "))
    if frame[1] & 0x80:
        raise ProtocolError("slave %d returned exception 0x%02X" % (frame[0], frame[2]))
    if frame[1] != FUNC_READ_HOLDING:
        raise ProtocolError("unexpected function code 0x%02X" % frame[1])
    byte_count = frame[2]
    if byte_count != count * 2:
        raise ProtocolError("byte count %d does not match %d registers" % (byte_count, count))
    data = frame[3:3 + byte_count]
    return tuple((data[i] << 8) | data[i + 1] for i in range(0, byte_count, 2))


def parse_write_response(frame: bytes, request: bytes) -> None:
    """校验写命令的回显应答。

    :raises ProtocolError: 回显与请求不一致
    """
    if len(frame) != WRITE_RESPONSE_LEN:
        raise ProtocolError("short write response: %d bytes" % len(frame))
    if not check_crc(frame):
        raise ProtocolError("CRC mismatch on write response: %s" % frame.hex(" "))
    if frame[:6] != request[:6]:
        raise ProtocolError(
            "write echo mismatch: sent %s, got %s" % (request[:6].hex(" "), frame[:6].hex(" "))
        )


__all__ = [
    "FUNC_READ_HOLDING",
    "FUNC_WRITE_SINGLE",
    "FUNC_WRITE_MULTIPLE",
    "BANK_SIZE",
    "WRITE_RESPONSE_LEN",
    "crc16_modbus",
    "append_crc",
    "check_crc",
    "to_u16",
    "s16",
    "build_read_holding",
    "build_write_single",
    "build_write_multiple",
    "expected_read_length",
    "parse_read_holding",
    "parse_write_response",
]
