"""Web GUI 服务：纯标准库的 HTTP + SSE 服务端（零新增依赖）。

启动::

    python -m webui                       # 默认 http://127.0.0.1:8765
    python -m webui --http-port 9000 --open
    python -m webui --serial COM8 --dry-run

接口一览
--------

======================  ======  ==================================================
路径                     方法    说明
======================  ======  ==================================================
``/``                    GET     控制页面
``/static/<file>``       GET     静态资源
``/api/model``           GET     关节语义 + 3D 几何（来自 ``model_config.py``）
``/api/state``           GET     连接状态 + 上次状态快照
``/api/ports``           GET     可用串口列表
``/api/logs``            GET     历史日志
``/api/events``          GET     **SSE**：log / trace / status / state
``/api/connect``         POST    连接串口
``/api/disconnect``      POST    断开（会先紧急停止）
``/api/verify``          POST    校验 6 个关节身份
``/api/refresh``         POST    只读刷新状态
``/api/estop``           POST    紧急停止
``/api/read``            POST    Debug：直接读寄存器
``/api/move``            POST    伺服关节移动
``/api/servo/stop``      POST    伺服失能
``/api/servo/enable``    POST    伺服使能
``/api/j1/rotate``       POST    J1 方向脉冲
``/api/j1/stop``         POST    J1 停止
``/api/j2/pulse``        POST    J2 上/下行脉冲
``/api/j2/stop``         POST    J2 停止
``/api/gripper``         POST    夹爪开/合
``/api/gripper/stop``    POST    夹爪停止
======================  ======  ==================================================

**所有会产生动作的接口都要求请求体里带 ``"confirm": true``** —— 这是防止误触的
第二道闸门（第一道在 SDK 传输层）。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import posixpath
import queue
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from . import model_config, model_store
from .controller import ArmController, list_serial_ports

STATIC_DIR = Path(__file__).resolve().parent / "static"

#: 需要显式确认的接口
CONFIRM_REQUIRED = {
    "/api/move", "/api/servo/stop", "/api/servo/enable", "/api/servo/limits",
    "/api/j1/rotate", "/api/j1/stop", "/api/j2/pulse", "/api/j2/stop",
    "/api/gripper", "/api/gripper/stop", "/api/estop",
    # 持久化会改变下次启动的默认值（包括软限位这个安全边界），也要确认
    "/api/model/save", "/api/model/reset",
}

MAX_BODY = 1 << 20          # 1 MiB
SSE_KEEPALIVE = 15.0        # 秒


class ArmHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, controller: ArmController):
        super().__init__(addr, ArmRequestHandler)
        self.controller = controller


class ArmRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "RobotArmWebUI/1.0"

    # ---- 基础工具 --------------------------------------------------------
    @property
    def controller(self) -> ArmController:
        return self.server.controller  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:      # noqa: A003
        # 把 HTTP 访问日志降级到 debug，避免刷屏
        try:
            self.controller.log("debug", "http %s" % (fmt % args))
        except Exception:
            pass

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message: str, status: int = 400) -> None:
        self._send_json({"ok": False, "error": message}, status)

    def _read_json(self) -> Optional[Dict[str, Any]]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > MAX_BODY:
            return None
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _require_confirm(self, path: str, body: Dict[str, Any]) -> bool:
        if path in CONFIRM_REQUIRED and not body.get("confirm"):
            self._send_error_json(
                "motion command rejected: request body must include \"confirm\": true",
                status=428)
            return False
        return True

    # ---- GET -------------------------------------------------------------
    def do_GET(self) -> None:      # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)

        try:
            if path in ("/", "/index.html"):
                return self._serve_static("index.html")
            if path.startswith("/static/"):
                return self._serve_static(path[len("/static/"):])
            if path == "/api/model":
                return self._send_json({"ok": True,
                                        "model": model_store.merged_model()})
            if path == "/api/state":
                return self._send_json({"ok": True, "state": self.controller.state()})
            if path == "/api/ports":
                return self._send_json({"ok": True, "ports": list_serial_ports()})
            if path == "/api/logs":
                limit = int(query.get("limit", ["300"])[0])
                return self._send_json({"ok": True,
                                        "logs": self.controller.recent_logs(limit)})
            if path == "/api/events":
                return self._serve_events()
            if path == "/api/ping":
                return self._send_json({"ok": True, "ts": time.time()})
        except BrokenPipeError:
            return
        except Exception as exc:  # noqa: BLE001
            try:
                self.controller.log("error", "GET %s failed: %s" % (path, exc))
            except Exception:
                pass
            return
        self._send_error_json("not found: %s" % path, status=404)

    def _serve_static(self, relative: str) -> None:
        # 防目录穿越
        safe = posixpath.normpath("/" + relative).lstrip("/")
        if not safe or safe.startswith("..") or os.path.isabs(safe):
            return self._send_error_json("bad path", status=400)
        target = (STATIC_DIR / safe).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            return self._send_error_json("not found: %s" % safe, status=404)
        ctype, _ = mimetypes.guess_type(str(target))
        if ctype is None:
            ctype = "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",
                                                  "application/json"):
            ctype += "; charset=utf-8"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # ---- SSE -------------------------------------------------------------
    def _serve_events(self) -> None:
        q = self.controller.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            def write_chunk(payload: str) -> None:
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()

            # 回放历史日志 + 当前状态
            for entry in self.controller.recent_logs(200):
                write_chunk("data: %s\n\n" % json.dumps(entry, ensure_ascii=False))
            write_chunk("data: %s\n\n" % json.dumps(
                {"type": "state", "ts": time.time(),
                 "data": self.controller.state()}, ensure_ascii=False))
            write_chunk(": connected\n\n")

            while True:
                try:
                    event = q.get(timeout=SSE_KEEPALIVE)
                except queue.Empty:
                    write_chunk(": keepalive\n\n")
                    continue
                write_chunk("data: %s\n\n" % json.dumps(event, ensure_ascii=False))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            self.controller.unsubscribe(q)
            self.close_connection = True

    # ---- POST ------------------------------------------------------------
    def do_POST(self) -> None:      # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_json()
        if body is None:
            return self._send_error_json("invalid JSON body", status=400)
        if not self._require_confirm(path, body):
            return

        handlers = {
            "/api/connect": self._h_connect,
            "/api/disconnect": self._h_disconnect,
            "/api/verify": self._h_verify,
            "/api/refresh": self._h_refresh,
            "/api/estop": self._h_estop,
            "/api/read": self._h_read,
            "/api/model/save": self._h_model_save,
            "/api/model/reset": self._h_model_reset,
            "/api/move": self._h_move,
            "/api/servo/stop": self._h_servo_stop,
            "/api/servo/enable": self._h_servo_enable,
            "/api/servo/limits": self._h_servo_limits,
            "/api/j1/rotate": self._h_j1_rotate,
            "/api/j1/stop": self._h_j1_stop,
            "/api/j2/pulse": self._h_j2_pulse,
            "/api/j2/stop": self._h_j2_stop,
            "/api/gripper": self._h_gripper,
            "/api/gripper/stop": self._h_gripper_stop,
        }
        handler = handlers.get(path)
        if handler is None:
            return self._send_error_json("not found: %s" % path, status=404)
        try:
            handler(body)
        except BrokenPipeError:
            return
        except ValueError as exc:
            self._send_error_json(str(exc), status=400)
        except KeyError as exc:
            self._send_error_json("missing field %s" % exc, status=400)
        except Exception as exc:  # noqa: BLE001
            self.controller.log("error", "POST %s failed: %s" % (path, exc))
            self._send_error_json(str(exc), status=500)

    # ---- 各接口实现 ------------------------------------------------------
    @staticmethod
    def _int(body: Dict[str, Any], key: str, default: Optional[int] = None) -> int:
        if key not in body or body[key] is None:
            if default is None:
                raise KeyError(key)
            return default
        return int(body[key])

    @staticmethod
    def _float(body: Dict[str, Any], key: str, default: float) -> float:
        if key not in body or body[key] is None:
            return default
        return float(body[key])

    @staticmethod
    def _str(body: Dict[str, Any], key: str, default: str = "") -> str:
        value = body.get(key, default)
        return default if value is None else str(value)

    def _h_connect(self, body: Dict[str, Any]) -> None:
        # 请求体里没给 dry_run 时用服务端启动参数里的默认值
        dry_run = body.get("dry_run")
        if dry_run is None:
            dry_run = self.controller.default_dry_run
        result = self.controller.connect(
            port=self._str(body, "port", self.controller.default_port) or None,
            baudrate=self._int(body, "baud", self.controller.default_baud),
            dry_run=bool(dry_run),
            timeout=self._float(body, "timeout", 1.0),
            retries=self._int(body, "retries", 3),
        )
        self._send_json(result, status=200 if result["ok"] else 502)

    def _h_disconnect(self, body: Dict[str, Any]) -> None:
        self._send_json(self.controller.disconnect(
            estop_first=bool(body.get("estop_first", True))))

    def _h_verify(self, body: Dict[str, Any]) -> None:
        self._send_json(self.controller.verify())

    def _h_refresh(self, body: Dict[str, Any]) -> None:
        self._send_json(self.controller.refresh(tries=self._int(body, "tries", 1)))

    def _h_estop(self, body: Dict[str, Any]) -> None:
        self._send_json(self.controller.estop())

    def _h_read(self, body: Dict[str, Any]) -> None:
        slave = self._int(body, "slave")
        start = self._int(body, "start", 0)
        count = self._int(body, "count", 0x18)
        if not 1 <= count <= 0x7D:
            raise ValueError("count must be 1..125")
        self._send_json(self.controller.raw_read(slave, start, count))

    def _h_move(self, body: Dict[str, Any]) -> None:
        joint = self._int(body, "joint")
        mode = self._str(body, "mode", "abs")
        value = body.get("value")
        if value is None:
            raise ValueError("missing field value")
        speed = body.get("speed")
        result = self.controller.servo_move(
            joint, mode=mode, value=float(value),
            speed=int(speed) if speed else None)
        self._send_json(result, status=200 if result["ok"] else 409)

    def _h_servo_stop(self, body: Dict[str, Any]) -> None:
        result = self.controller.servo_stop(self._int(body, "joint"))
        self._send_json(result, status=200 if result["ok"] else 409)

    def _h_servo_enable(self, body: Dict[str, Any]) -> None:
        result = self.controller.servo_enable(self._int(body, "joint"))
        self._send_json(result, status=200 if result["ok"] else 409)

    def _h_servo_limits(self, body: Dict[str, Any]) -> None:
        lo = self._int(body, "lo")
        hi = self._int(body, "hi")
        if lo >= hi:
            raise ValueError("lo must be < hi")
        if hi - lo > 20000:
            raise ValueError("range too wide (max 20000 counts)")
        result = self.controller.set_servo_limits(self._int(body, "joint"), lo, hi)
        self._send_json(result, status=200 if result["ok"] else 409)

    def _h_j1_rotate(self, body: Dict[str, Any]) -> None:
        result = self.controller.j1_rotate(self._str(body, "direction", "a"),
                                           self._float(body, "seconds", 2.5))
        self._send_json(result, status=200 if result["ok"] else 409)

    def _h_j1_stop(self, body: Dict[str, Any]) -> None:
        result = self.controller.j1_stop()
        self._send_json(result, status=200 if result["ok"] else 409)

    def _h_j2_pulse(self, body: Dict[str, Any]) -> None:
        result = self.controller.j2_pulse(self._str(body, "direction", "up"),
                                          self._float(body, "seconds", 2.0))
        self._send_json(result, status=200 if result["ok"] else 409)

    def _h_j2_stop(self, body: Dict[str, Any]) -> None:
        result = self.controller.j2_stop()
        self._send_json(result, status=200 if result["ok"] else 409)

    def _h_model_save(self, body: Dict[str, Any]) -> None:
        model = body.get("model")
        if not isinstance(model, dict):
            raise ValueError("missing field model")
        try:
            saved = model_store.save(model)
        except model_store.ModelOverrideError as exc:
            return self._send_error_json("模型不合法：%s" % exc, status=400)
        self.controller.log("warn", "模型参数已保存为默认（写入 %s）"
                            % os.path.basename(model_store.OVERRIDE_PATH))
        self.controller.apply_saved_limits()
        self._send_json({"ok": True, "saved": saved,
                         "model": model_store.merged_model()})

    def _h_model_reset(self, body: Dict[str, Any]) -> None:
        removed = model_store.clear()
        self.controller.log("info", "模型参数已重置为出厂值%s"
                            % ("（删除覆盖文件）" if removed else "（本来就没有覆盖文件）"))
        self.controller.apply_saved_limits()
        self._send_json({"ok": True, "removed": removed,
                         "model": model_store.merged_model()})

    def _h_gripper(self, body: Dict[str, Any]) -> None:
        from robot_arm.joints.gripper import DEG_PER_COUNT, FULL_OPEN_COUNTS

        action = self._str(body, "action", "open")
        if action not in ("open", "close", "set", "open_for", "close_for"):
            raise ValueError("action must be open / close / set / open_for / close_for")
        speed = body.get("speed")
        counts = body.get("counts")
        if counts is None and body.get("deg") is not None:
            counts = int(round(float(body["deg"]) / DEG_PER_COUNT))
        if counts is None and body.get("ratio") is not None:
            counts = int(round(float(body["ratio"]) * FULL_OPEN_COUNTS))
        if action == "set":
            if counts is None:
                raise ValueError("action=set requires counts / deg / ratio")
            counts = int(counts)
            if not 0 <= counts <= FULL_OPEN_COUNTS:
                raise ValueError("counts must be 0..%d" % FULL_OPEN_COUNTS)
        seconds = body.get("seconds")
        if action in ("open_for", "close_for"):
            if seconds is None:
                raise ValueError("action=%s requires seconds" % action)
            seconds = float(seconds)
            if not 0 < seconds <= 60:
                raise ValueError("seconds must be in (0, 60]")
        result = self.controller.gripper(
            action,
            int(speed) if speed else None,
            int(counts) if counts is not None else None,
            seconds,
        )
        self._send_json(result, status=200 if result["ok"] else 409)

    def _h_gripper_stop(self, body: Dict[str, Any]) -> None:
        result = self.controller.gripper_stop()
        self._send_json(result, status=200 if result["ok"] else 409)


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def serve(host: str = "127.0.0.1", http_port: int = model_config.DEFAULT_HTTP_PORT,
          serial_port: str = model_config.DEFAULT_SERIAL_PORT,
          baud: int = model_config.DEFAULT_BAUD, dry_run: bool = False,
          open_browser: bool = False, auto_connect: bool = False) -> int:
    """启动服务（阻塞）。

    ``dry_run=True`` 会让**页面上的 dry-run 勾选框默认打开**，即使页面不传
    ``dry_run`` 字段、直接点连接，也不会真正下发写命令。
    """
    controller = ArmController(default_port=serial_port, default_baud=baud,
                              default_dry_run=dry_run)
    controller.log("info", "RobotArm Web UI 启动 (host=%s port=%d dry_run=%s auto_connect=%s)"
                   % (host, http_port, dry_run, auto_connect))
    if auto_connect:
        controller.connect(serial_port, baud, dry_run=dry_run)

    server = ArmHTTPServer((host, http_port), controller)
    url = "http://%s:%d/" % (host if host != "0.0.0.0" else "127.0.0.1", http_port)
    print("=" * 62)
    print(" 机械臂 Web 控制台")
    print("   本机:  %s" % url)
    if host == "0.0.0.0":
        print("   局域网: http://%s:%d/" % (_lan_ip(), http_port))
    print("   串口:  %s @ %d%s" % (serial_port, baud,
                                   "  [DRY-RUN 已预设]" if dry_run else ""))
    if dry_run and not auto_connect:
        print("   注意: --dry-run 只是预设了页面上的勾选框；页面里取消勾选即可真实控制。")
    if not auto_connect:
        print("   提示: 页面里点『连接』才会打开串口（或加 --auto-connect 启动即连）。")
    print("   Ctrl+C 停止")
    print("=" * 62)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\n正在停止 ...")
    finally:
        server.shutdown()
        controller.shutdown()
        server.server_close()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="webui", description="机械臂 Web 控制台")
    ap.add_argument("--host", default="127.0.0.1",
                    help="监听地址（0.0.0.0 允许局域网访问）")
    ap.add_argument("--http-port", type=int, default=model_config.DEFAULT_HTTP_PORT)
    ap.add_argument("--serial", default=model_config.DEFAULT_SERIAL_PORT,
                    help="默认串口（页面里也可以改）")
    ap.add_argument("--baud", type=int, default=model_config.DEFAULT_BAUD)
    ap.add_argument("--dry-run", action="store_true",
                    help="默认以 dry-run 连接（不真正下发写命令）")
    ap.add_argument("--auto-connect", action="store_true", help="启动时就连接串口")
    ap.add_argument("--open", action="store_true", help="自动打开浏览器")
    args = ap.parse_args(argv)
    return serve(args.host, args.http_port, args.serial, args.baud,
                 args.dry_run, args.open, args.auto_connect)


__all__ = ["serve", "main", "ArmHTTPServer", "ArmRequestHandler"]


if __name__ == "__main__":
    raise SystemExit(main())
