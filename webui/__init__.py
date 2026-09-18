"""机械臂 Web 控制台（3D 数字孪生 + 关节控制 + 日志/Debug）。

启动::

    python -m webui --open

模块划分::

    model_config.py   关节语义 + 3D 几何（唯一数据源，改这里就能改模型）
    controller.py     工作线程 + 作业队列 + 事件总线（独占串口）
    server.py         纯标准库 HTTP + SSE 服务
    static/            前端（原生 JS + 自写 WebGL，无 CDN 依赖）
"""

from __future__ import annotations

from .controller import ArmController, list_serial_ports
from .server import serve

__version__ = "1.0.0"

__all__ = ["ArmController", "list_serial_ports", "serve", "__version__"]
