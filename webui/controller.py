"""Web GUI 的控制器：把 SDK 包成"单线程串行 + 事件广播"的服务。

设计要点
--------

1. **一个工作线程独占串口**。所有会碰总线的操作都排进队列，由工作线程串行执行，
   HTTP 处理线程只负责入队/等待。这样既避免了多客户端并发访问串口，
   也天然满足"运动期间不轮询"的要求（运动是一个作业，期间没有别的读）。
2. **事件总线**。日志、原始帧 trace、状态快照、连接状态变化都以事件形式
   推给所有 SSE 订阅者。
3. **环形日志缓冲**。新连上的页面能立刻看到历史（最近 2000 条）。
4. 安全策略不在这一层实现 —— 它在 :mod:`robot_arm` 传输层，
   本层无法绕过。
"""

from __future__ import annotations

import collections
import itertools
import json
import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from robot_arm import ArmError, RobotArm, SafetyError
from robot_arm.transport import DEFAULT_BAUDRATE, DEFAULT_PORT

from . import model_config

#: 日志环形缓冲长度
LOG_BUFFER = 2000

#: 单个 SSE 订阅者的队列长度（满了丢最旧的）
SUBSCRIBER_QUEUE = 500

LOG_LEVELS = ("debug", "info", "warn", "error", "sdk", "trace")


@dataclass
class Job:
    """一个要在工作线程上串行执行的操作。

    ``needs_arm=False`` 的作业在建连/断连时使用 —— 那时 ``self._arm`` 可能为 ``None``。
    """

    fn: Callable[[Optional[RobotArm]], Any]
    description: str
    needs_arm: bool = True
    silent: bool = False
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Optional[str] = None
    started: float = 0.0
    finished: float = 0.0

    @property
    def elapsed(self) -> float:
        if not self.started:
            return 0.0
        end = self.finished or time.monotonic()
        return round(end - self.started, 3)


class ArmController:
    """线程安全的机械臂服务门面。"""

    def __init__(self, *, default_port: str = DEFAULT_PORT,
                 default_baud: int = DEFAULT_BAUDRATE,
                 default_dry_run: bool = False):
        self.default_port = default_port
        self.default_baud = default_baud
        #: ``--dry-run`` 启动时置 True：页面未显式指定 dry_run 时用它
        self.default_dry_run = bool(default_dry_run)

        self._lock = threading.RLock()
        self._arm: Optional[RobotArm] = None
        self._status: Dict[str, dict] = {}
        self._conn: Dict[str, Any] = {
            "connected": False, "port": None, "baud": default_baud,
            "dry_run": False, "last_error": None, "since": None,
        }

        self._jobs: "queue.Queue[Optional[Job]]" = queue.Queue()
        self._busy = False
        self._current_job: Optional[str] = None

        self._subscribers: "set[queue.Queue]" = set()
        self._sub_lock = threading.Lock()
        self._seq = itertools.count(1)

        self._logs: "collections.deque[dict]" = collections.deque(maxlen=LOG_BUFFER)

        self._worker = threading.Thread(target=self._worker_loop, name="arm-worker",
                                        daemon=True)
        self._worker.start()

    # ==================================================================
    # 事件总线
    # ==================================================================
    def subscribe(self) -> "queue.Queue":
        q: "queue.Queue" = queue.Queue(maxsize=SUBSCRIBER_QUEUE)
        with self._sub_lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: "queue.Queue") -> None:
        with self._sub_lock:
            self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        with self._sub_lock:
            return len(self._subscribers)

    def _emit(self, event_type: str, data: Any = None, **extra) -> dict:
        event = {"seq": next(self._seq), "ts": round(time.time(), 3),
                 "type": event_type}
        if data is not None:
            event["data"] = data
        event.update(extra)
        with self._sub_lock:
            targets = list(self._subscribers)
        for q in targets:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:                       # 丢最旧的一条再放
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    pass
        return event

    def log(self, level: str, message: str, **data) -> None:
        """记录一条日志（同时进环形缓冲与事件流）。"""
        entry = {"seq": next(self._seq), "ts": round(time.time(), 3),
                 "type": "log", "level": level, "message": message}
        if data:
            entry["data"] = data
        self._logs.append(entry)
        self._emit_entry(entry)

    def _emit_entry(self, entry: dict) -> None:
        with self._sub_lock:
            targets = list(self._subscribers)
        for q in targets:
            try:
                q.put_nowait(entry)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(entry)
                except Exception:
                    pass

    def recent_logs(self, limit: int = 300) -> List[dict]:
        items = list(self._logs)
        return items[-limit:]

    # SDK 回调（由工作线程调用）
    def _sdk_log(self, message: str) -> None:
        self.log("sdk", message)

    def _sdk_trace(self, direction: str, data: bytes) -> None:
        self._emit("trace", {"dir": direction, "hex": data.hex(" "),
                             "len": len(data)}, level="trace")

    # ==================================================================
    # 作业队列
    # ==================================================================
    def submit(self, fn: Callable[[Optional[RobotArm]], Any], description: str, *,
               wait: bool = True, timeout: float = 30.0,
               silent: bool = False, needs_arm: bool = True) -> dict:
        """把操作排进队列。

        :param wait: True 时阻塞等待结果（适合快速命令）；False 立即返回
        :param needs_arm: False 表示未连接也允许执行（建连/断连用）
        :returns: ``{"ok": bool, "result": ..., "error": ...}``
        """
        job = Job(fn=fn, description=description, silent=silent, needs_arm=needs_arm)
        self._jobs.put(job)
        if not wait:
            return {"ok": True, "queued": True, "description": description}
        if not job.event.wait(timeout):
            return {"ok": False, "error": "timeout waiting for %r" % description,
                    "timeout": True}
        if job.error:
            return {"ok": False, "error": job.error}
        return {"ok": True, "result": job.result}

    def _worker_loop(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            arm = self._arm
            if job.needs_arm and arm is None:
                job.error = "not connected"
            else:
                job.started = time.monotonic()
                with self._lock:
                    self._busy = True
                    self._current_job = job.description
                self._emit("state", self.state())
                if not job.silent:
                    self.log("info", "-> %s" % job.description)
                try:
                    job.result = job.fn(arm)
                except SafetyError as exc:
                    job.error = "REFUSED: %s" % exc
                    self.log("warn", "safety refused: %s" % job.description,
                             detail=str(exc))
                except ArmError as exc:
                    job.error = "%s: %s" % (type(exc).__name__, exc)
                    self.log("error", "%s failed" % job.description, detail=job.error)
                except Exception as exc:  # noqa: BLE001 - 兜底，避免工作线程死掉
                    job.error = "%s: %s" % (type(exc).__name__, exc)
                    self.log("error", "unexpected error in %s" % job.description,
                             detail=traceback.format_exc())
                finally:
                    job.finished = time.monotonic()
                    with self._lock:
                        self._busy = False
                        self._current_job = None
                    if job.error is None and not job.silent:
                        self.log("info", "<- %s ok (%.2fs)" % (job.description, job.elapsed))
            job.event.set()
            self._emit("state", self.state())

    # ==================================================================
    # 状态
    # ==================================================================
    def state(self) -> dict:
        with self._lock:
            conn = dict(self._conn)
            conn["busy"] = self._busy
            conn["current_job"] = self._current_job
            conn["queued"] = self._jobs.qsize()
            conn["subscribers"] = self.subscriber_count
            conn["status"] = self._status
            conn["dry_run"] = conn.get("dry_run", False)
            conn["default_dry_run"] = self.default_dry_run
            conn["default_port"] = self.default_port
            conn["default_baud"] = self.default_baud
            return conn

    def _store_status(self, status: Dict[str, dict]) -> Dict[str, dict]:
        with self._lock:
            self._status = status
        return status

    # ==================================================================
    # 连接管理
    # ==================================================================
    def connect(self, port: Optional[str] = None, baudrate: Optional[int] = None,
                dry_run: Optional[bool] = None, timeout: float = 1.0,
                retries: int = 3) -> dict:
        port = port or self.default_port
        baudrate = baudrate or self.default_baud
        # 页面没传 dry_run 时用启动参数里的默认值（不要默默变成"真的会动"）
        if dry_run is None:
            dry_run = self.default_dry_run
        dry_run = bool(dry_run)
        self.log("info", "正在连接 %s @ %d ..." % (port, baudrate),
                 dry_run=dry_run)

        def _do(_arm: Optional[RobotArm]) -> dict:
            self._close_arm_locked()
            arm = RobotArm(
                port, baudrate=baudrate, timeout=timeout, retries=retries,
                retry_delay=0.2, dry_run=dry_run, verbose=False,
                logger=self._sdk_log, trace=self._sdk_trace,
            )
            arm.open()
            with self._lock:
                self._arm = arm
                self._conn.update({"connected": True, "port": port,
                                   "baud": baudrate, "dry_run": dry_run,
                                   "last_error": None, "since": time.time()})
            self.apply_saved_limits()
            status = arm.status(tries=1)
            self._store_status(status)
            return {"port": port, "baud": baudrate, "dry_run": dry_run,
                    "online": sum(1 for s in status.values() if s.get("online"))}

        result = self.submit(_do, "connect %s" % port, wait=True, timeout=15.0,
                             needs_arm=False)
        if result["ok"]:
            online = result["result"]["online"]
            self.log("info", "已连接 %s%s，在线关节 %d/6"
                     % (port, " [DRY-RUN 不会真的下发写命令]" if dry_run else "", online))
            if online == 0:
                self.log("warn", "总线无应答：机械臂可能未上电，或串口/线缆不对")
        else:
            with self._lock:
                self._conn["last_error"] = result["error"]
                self._conn["connected"] = False
            self.log("error", "连接失败: %s" % result["error"])
        self._emit("state", self.state())
        return result

    def apply_saved_limits(self) -> dict:
        """把 ``model_overrides.json`` 里保存的软限位装到当前连接上。

        在建连时、以及保存/重置模型后调用 —— 这样页面上看到的滑条范围
        和 SDK 实际执行的边界总是一致。
        """
        from . import model_store

        with self._lock:
            arm = self._arm
        if arm is None:
            return {}
        try:
            applied = model_store.apply_soft_limits(arm)
        except Exception as exc:  # noqa: BLE001
            self.log("warn", "应用已保存的软限位失败: %s" % exc)
            return {}
        if applied:
            self.log("info", "已应用保存的软限位: %s" % applied)
        return applied

    def _close_arm_locked(self) -> None:
        arm = self._arm
        self._arm = None
        if arm is not None:
            try:
                arm.close()
            except Exception:
                pass

    def disconnect(self, *, estop_first: bool = True) -> dict:
        def _do(arm: Optional[RobotArm]) -> dict:
            summary = None
            if arm is not None and estop_first:
                try:
                    summary = arm.estop()
                except Exception as exc:
                    self.log("warn", "断开前的紧急停止失败: %s" % exc)
            self._close_arm_locked()
            with self._lock:
                self._conn.update({"connected": False, "port": None,
                                   "last_error": None, "since": None})
                self._status = {}
            return {"estop": summary}

        result = self.submit(_do, "disconnect", wait=True, timeout=20.0,
                             needs_arm=False)
        self.log("info", "已断开连接")
        self._emit("state", self.state())
        return result

    # ==================================================================
    # 只读操作
    # ==================================================================
    def refresh(self, tries: int = 1) -> dict:
        """刷新状态快照（供页面轮询）。"""

        def _do(arm: RobotArm) -> Dict[str, dict]:
            status = arm.status(tries=tries)
            self._store_status(status)
            self._emit("status", status)
            return status

        return self.submit(_do, "refresh", wait=True, timeout=15.0, silent=True)

    def verify(self) -> dict:
        """校验全部关节身份（完整重试）。"""

        def _do(arm: RobotArm) -> Dict[str, dict]:
            results = arm.verify_all(raise_on_error=False)
            self._store_status(arm.status(tries=1))
            return results

        result = self.submit(_do, "verify identity", wait=True, timeout=40.0)
        if result["ok"]:
            bad = [k for k, v in result["result"].items() if not v.get("ok")]
            if bad:
                self.log("warn", "身份校验失败: %s" % ", ".join(bad))
            else:
                self.log("info", "身份校验通过：6/6")
        return result

    def raw_read(self, slave: int, start: int, count: int) -> dict:
        """Debug 用：直接读寄存器。"""

        def _do(arm: RobotArm) -> dict:
            words = arm.transport.read_registers(slave, start, count)
            joint = model_config.JOINT_BY_SLAVE.get(slave)
            return {
                "slave": slave, "start": start, "count": count,
                "joint": joint["name"] if joint else None,
                "words": list(words),
                "hex": ["0x%04X" % w for w in words],
            }

        return self.submit(_do, "raw read slave%d [%d..%d]" % (slave, start, start + count - 1),
                           wait=True, timeout=15.0)

    # ==================================================================
    # 控制操作
    # ==================================================================
    def estop(self) -> dict:
        """紧急停止。任何时候都能调用，不需要先校验身份。"""

        def _do(arm: RobotArm) -> dict:
            results = arm.estop()
            self._store_status(arm.status(tries=1))
            return results

        # 不等待：即使总线正忙也要尽快排队；但 estop 需要独占总线，因此仍走队列
        result = self.submit(_do, "EMERGENCY STOP", wait=True, timeout=25.0)
        self.log("warn", "紧急停止已下发" if result["ok"] else
                 "紧急停止失败: %s" % result.get("error"))
        return result

    def servo_move(self, joint_id: int, *, mode: str, value: float,
                   speed: Optional[int] = None) -> dict:
        """位置伺服关节移动。``mode`` = ``abs`` / ``rel`` / ``counts`` / ``rel_counts``。"""
        spec = model_config.joint_by_id(joint_id)
        if spec["control"] != "servo":
            return {"ok": False, "error": "joint %d is not a servo joint" % joint_id}

        def _do(arm: RobotArm) -> dict:
            joint = arm[spec["slave"]]
            joint.verify(tries=2)
            if speed:
                joint.set_speed(int(speed))
            if mode == "abs":
                state = joint.move_to_deg(float(value))
            elif mode == "rel":
                state = joint.move_rel_deg(float(value))
            elif mode == "counts":
                state = joint.move_to_counts(int(value))
            elif mode == "rel_counts":
                state = joint.move_rel_counts(int(value))
            else:
                raise ValueError("unknown move mode %r" % mode)
            self._store_status(arm.status(tries=1))
            return state.as_dict()

        label = "%s %s %s" % (spec["name"], mode, value)
        return self.submit(_do, "move %s" % label, wait=True, timeout=90.0)

    def servo_stop(self, joint_id: int) -> dict:
        spec = model_config.joint_by_id(joint_id)

        def _do(arm: RobotArm) -> dict:
            arm[spec["slave"]].stop()
            self._store_status(arm.status(tries=1))
            return {"stopped": spec["name"]}

        return self.submit(_do, "stop %s (drive off)" % spec["name"],
                           wait=True, timeout=20.0)

    def servo_enable(self, joint_id: int) -> dict:
        spec = model_config.joint_by_id(joint_id)

        def _do(arm: RobotArm) -> dict:
            arm[spec["slave"]].enable()
            self._store_status(arm.status(tries=1))
            return {"enabled": spec["name"]}

        return self.submit(_do, "enable %s" % spec["name"], wait=True, timeout=20.0)

    def j1_rotate(self, direction: str, seconds: float) -> dict:
        spec = model_config.joint_by_id(1)

        def _do(arm: RobotArm) -> dict:
            state = arm.j1.rotate(direction, seconds)
            self._store_status(arm.status(tries=1))
            return state.as_dict()

        return self.submit(_do, "J1 rotate %s %.2fs" % (direction, seconds),
                           wait=True, timeout=max(30.0, seconds + 20))

    def j1_stop(self) -> dict:
        def _do(arm: RobotArm) -> dict:
            arm.j1.stop()
            self._store_status(arm.status(tries=1))
            return {"stopped": "J1"}

        return self.submit(_do, "stop J1 (torque removed)", wait=True, timeout=20.0)

    def j2_pulse(self, direction: str, seconds: float) -> dict:
        def _do(arm: RobotArm) -> dict:
            state = arm.j2.up(seconds) if direction == "up" else arm.j2.down(seconds)
            self._store_status(arm.status(tries=1))
            return state.as_dict()

        return self.submit(_do, "J2 %s pulse %.2fs" % (direction, seconds),
                           wait=True, timeout=max(30.0, seconds + 20))

    def j2_stop(self) -> dict:
        def _do(arm: RobotArm) -> dict:
            arm.j2.stop()
            self._store_status(arm.status(tries=1))
            return {"stopped": "J2"}

        return self.submit(_do, "stop J2", wait=True, timeout=20.0)

    def gripper(self, action: str, speed: Optional[int] = None,
                counts: Optional[int] = None,
                seconds: Optional[float] = None) -> dict:
        """夹爪控制。

        ``action``：
          * ``open`` / ``close`` —— 全开 / 全闭（闭环，位置码到位）
          * ``set``               —— 走到指定位置码（配合 ``counts``；目前只有 0/90 有效）
          * ``open_for`` / ``close_for`` —— **定时开合**（中间开度的唯一方式）
        """
        spec = model_config.joint_by_id(6)

        def _do(arm: RobotArm) -> dict:
            joint = arm.j6
            joint.verify(tries=2)
            if speed:
                joint.speed = int(speed)
            if action == "open":
                state = joint.open()
            elif action == "close":
                state = joint.close()
            elif action == "set":
                if counts is None:
                    raise ValueError("action=set requires counts")
                state = joint.open_to_counts(int(counts))
            elif action == "open_for":
                state = joint.open_for(float(seconds))
            elif action == "close_for":
                state = joint.close_for(float(seconds))
            else:
                raise ValueError("unknown gripper action %r" % action)
            self._store_status(arm.status(tries=1))
            return state.as_dict()

        label = "J6 %s" % action
        if action == "set":
            label += " -> %s counts" % counts
        elif action in ("open_for", "close_for"):
            label += " %.2fs" % float(seconds)
        return self.submit(_do, label, wait=True, timeout=40.0)

    def gripper_stop(self) -> dict:
        def _do(arm: RobotArm) -> dict:
            arm.j6.stop()
            self._store_status(arm.status(tries=1))
            return {"stopped": "J6"}

        return self.submit(_do, "stop J6 (grip released)", wait=True, timeout=20.0)

    def set_servo_limits(self, joint_id: int, lo: int, hi: int) -> dict:
        """修改伺服关节的软限位（计数，相对上电零点）。

        这是安全相关设置：只改 SDK 侧的检查，不下发任何总线写。
        调用方（HTTP 层）已用运动锁 + confirm 闸门把关。
        """
        spec = model_config.joint_by_id(joint_id)
        if spec["control"] != "servo":
            return {"ok": False, "error": "joint %d is not a servo joint" % joint_id}

        def _do(arm: RobotArm) -> dict:
            joint = arm[spec["slave"]]
            joint.set_limits(int(lo), int(hi))
            return {"joint": joint_id, "slave": spec["slave"],
                    "limits": [int(lo), int(hi)],
                    "limits_deg": [round(joint.counts_to_deg(int(lo)), 2),
                                   round(joint.counts_to_deg(int(hi)), 2)]}

        return self.submit(_do, "set %s soft limits [%d, %d]" % (spec["name"], lo, hi),
                           wait=True, timeout=15.0)

    # ==================================================================
    # 收尾
    # ==================================================================
    def shutdown(self) -> None:
        self.log("info", "服务关闭")
        try:
            if self._arm is not None:
                self.submit(lambda arm: arm.estop(), "shutdown estop",
                            wait=True, timeout=10.0, silent=True)
        except Exception:
            pass
        self._jobs.put(None)
        try:
            self._close_arm_locked()
        except Exception:
            pass


def list_serial_ports() -> List[dict]:
    """列出可用串口（供页面下拉框）。"""
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    ports = []
    for info in list_ports.comports():
        ports.append({"device": info.device,
                      "description": info.description or "",
                      "hwid": info.hwid or ""})
    return ports


__all__ = ["ArmController", "list_serial_ports", "Job", "LOG_LEVELS"]
