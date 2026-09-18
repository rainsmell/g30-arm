"""零碎工具：蜂鸣提示、日志。

注意：运行期输出一律使用 **ASCII 字符**，避免在 GBK 控制台下抛 UnicodeEncodeError。
"""

from __future__ import annotations

import sys
import time
from typing import Callable, Optional

Logger = Callable[[str], None]


def silent_logger(_message: str) -> None:
    """默认日志器：什么都不做。"""


def stdout_logger(message: str) -> None:
    """把日志打到 stdout。"""
    print(message, flush=True)


def stderr_logger(message: str) -> None:
    """把日志打到 stderr。"""
    print(message, file=sys.stderr, flush=True)


def beep(enabled: bool = True, frequency: int = 1400, duration_ms: int = 300) -> None:
    """动作前的提示音（可选）。

    优先用 ``winsound``（Windows）；失败则退回 ``\\a``。
    """
    if not enabled:
        return
    try:
        import winsound

        winsound.Beep(frequency, duration_ms)
    except Exception:
        try:
            sys.stdout.write("\a")
            sys.stdout.flush()
        except Exception:
            pass
    if enabled:
        # 保证提示音先于任何后续输出
        time.sleep(0.05)


class Beeper:
    """可开关的提示音对象，方便全局禁用（例如自动化脚本里）。"""

    def __init__(self, enabled: bool = True, frequency: int = 1400, duration_ms: int = 300):
        self.enabled = enabled
        self.frequency = frequency
        self.duration_ms = duration_ms

    def __call__(self) -> None:
        beep(self.enabled, self.frequency, self.duration_ms)

    def mute(self) -> None:
        self.enabled = False

    def unmute(self) -> None:
        self.enabled = True


def default_logger(verbose: bool) -> Logger:
    return stdout_logger if verbose else silent_logger


__all__ = [
    "Logger",
    "silent_logger",
    "stdout_logger",
    "stderr_logger",
    "beep",
    "Beeper",
    "default_logger",
]
