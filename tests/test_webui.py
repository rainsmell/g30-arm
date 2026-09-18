"""Web GUI 的离线测试：真实 HTTP 服务 + 真实 SDK + 假串口。

    python tests/test_webui.py

覆盖：
  * 路由与静态资源（含目录穿越防护）
  * 运动命令的 confirm 闸门
  * 控制器作业队列 / 事件总线 / 日志环形缓冲
  * 连接 -> 移动 -> 急停 的端到端流程（走真实 RobotArm，只把串口换成假的）
  * SSE 事件流
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_offline                                     # noqa: E402
from robot_arm import RobotArm                          # noqa: E402
from webui import controller as controller_mod          # noqa: E402
from webui import model_store                           # noqa: E402
from webui.controller import ArmController               # noqa: E402
from webui.server import ArmHTTPServer, serve            # noqa: E402
from webui import model_config                           # noqa: E402

FakeTransport = test_offline.FakeTransport


# ------------------------------------------------------------------ 工具
# 环境里可能配了 HTTP_PROXY（本测试只访问 127.0.0.1），显式禁用代理。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(method: str, url: str, body=None, timeout: float = 20.0):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            if "json" in ctype:
                return resp.status, json.loads(raw.decode("utf-8")), dict(resp.headers)
            return resp.status, raw, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw.decode("utf-8")), dict(exc.headers)
        except Exception:
            return exc.code, raw, dict(exc.headers)


class ServerFixture:
    """在后台线程跑一个真实的 HTTP 服务。"""

    def __init__(self, fake_serial: bool = True, default_dry_run: bool = False):
        self.fake_serial = fake_serial
        self.controller = ArmController(default_port="COM_TEST", default_baud=115200,
                                        default_dry_run=default_dry_run)
        self.server = ArmHTTPServer(("127.0.0.1", 0), self.controller)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)

    @property
    def base(self) -> str:
        return "http://127.0.0.1:%d" % self.port

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


_original_robot_arm = controller_mod.RobotArm


def fake_robot_arm_factory(port, **kwargs):
    """把控制器里的 RobotArm 换成"真实 SDK + 假串口"。"""
    transport = FakeTransport(dry_run=kwargs.get("dry_run", False))
    arm = RobotArm(transport=transport, trace=kwargs.get("trace"))
    return arm


class TestDryRun(unittest.TestCase):
    """``--dry-run`` 必须真的能挡住写命令，不能只是个装饰。

    背景：早期实现里 ``--dry-run`` 只在 ``--auto-connect`` 时才生效，
    页面上取消勾选就会真的驱动机器 —— 对执行器来说很危险。
    """

    def setUp(self):
        self._orig = controller_mod.RobotArm
        controller_mod.RobotArm = fake_robot_arm_factory
        self.ctl = ArmController(default_port="COM_TEST", default_dry_run=True)

    def tearDown(self):
        self.ctl.shutdown()
        controller_mod.RobotArm = self._orig

    def test_default_is_exposed_in_state(self):
        self.assertTrue(self.ctl.state()["default_dry_run"])

    def test_connect_without_explicit_flag_stays_dry(self):
        out = self.ctl.connect("COM_TEST", 115200)
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["result"]["dry_run"])

    def test_no_write_reaches_the_wire(self):
        self.ctl.connect("COM_TEST", 115200)
        out = self.ctl.servo_move(3, mode="rel", value=5.0)
        self.assertTrue(out["ok"], out)
        arm = self.ctl._arm
        self.assertEqual(arm.transport.writes, [], "dry-run 下不应有真实写操作")
        self.assertTrue(any(reg == 0x0B for _, reg, _ in arm.transport.planned_writes))

    def test_explicit_false_overrides_the_default(self):
        out = self.ctl.connect("COM_TEST", 115200, dry_run=False)
        self.assertFalse(out["result"]["dry_run"])
        arm = self.ctl._arm
        self.ctl.servo_move(3, mode="rel", value=5.0)
        self.assertTrue(arm.transport.writes, "显式 dry_run=False 时应真的写")

    def test_log_mentions_dry_run(self):
        self.ctl.connect("COM_TEST", 115200)
        text = " ".join(e["message"] for e in self.ctl.recent_logs(50))
        self.assertIn("DRY-RUN", text)


class TestDryRunOverHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig = controller_mod.RobotArm
        controller_mod.RobotArm = fake_robot_arm_factory
        cls.fx = ServerFixture(default_dry_run=True).start()
        cls.base = cls.fx.base

    @classmethod
    def tearDownClass(cls):
        cls.fx.stop()
        controller_mod.RobotArm = cls._orig

    def test_connect_without_body_flag_keeps_dry_run(self):
        status, data, _ = http("POST", self.base + "/api/connect",
                               {"port": "COM_TEST", "baud": 115200})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"], data)
        self.assertTrue(data["result"]["dry_run"])

    def test_move_does_not_write_to_the_wire(self):
        status, data, _ = http("POST", self.base + "/api/move",
                               {"joint": 3, "mode": "rel", "value": 5,
                                "confirm": True})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"], data)
        arm = self.fx.controller._arm
        self.assertEqual(arm.transport.writes, [])

    def test_state_endpoint_reports_the_default(self):
        status, data, _ = http("GET", self.base + "/api/state")
        self.assertTrue(data["state"]["default_dry_run"])

    def test_servo_limits_needs_confirm(self):
        status, data, _ = http("POST", self.base + "/api/servo/limits",
                               {"joint": 4, "lo": -400, "hi": 400})
        self.assertEqual(status, 428)
        self.assertIn("confirm", data["error"])

    def test_servo_limits_rejects_bad_range(self):
        for lo, hi in ((100, 100), (500, -500)):
            with self.subTest(lo=lo, hi=hi):
                status, _, _ = http("POST", self.base + "/api/servo/limits",
                                    {"joint": 4, "lo": lo, "hi": hi,
                                     "confirm": True})
                self.assertEqual(status, 400)


# ------------------------------------------------------------------ 路由
class TestRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = ServerFixture().start()
        cls.base = cls.fx.base

    @classmethod
    def tearDownClass(cls):
        cls.fx.stop()

    def test_index_html(self):
        status, body, headers = http("GET", self.base + "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<canvas", body)
        self.assertIn("text/html", headers.get("Content-Type", ""))

    def test_static_assets(self):
        for name in ("style.css", "app.js", "viewer.js", "mat4.js", "model.js"):
            with self.subTest(asset=name):
                status, body, _ = http("GET", self.base + "/static/" + name)
                self.assertEqual(status, 200)
                self.assertGreater(len(body), 100)

    def test_static_traversal_blocked(self):
        for path in ("/static/../server.py", "/static/..%2fserver.py",
                     "/static/../../etc/passwd"):
            with self.subTest(path=path):
                status, body, _ = http("GET", self.base + path)
                self.assertIn(status, (400, 404))

    def test_model_endpoint(self):
        status, data, _ = http("GET", self.base + "/api/model")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        model = data["model"]
        self.assertEqual(len(model["joints"]), 6)
        self.assertGreaterEqual(len(model["chain"]), 6)
        for joint in model["joints"]:
            self.assertIn("id", joint)
            self.assertIn("range_deg", joint)
            self.assertIn("control", joint)

    def test_ports_endpoint(self):
        status, data, _ = http("GET", self.base + "/api/ports")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertIsInstance(data["ports"], list)

    def test_state_endpoint(self):
        status, data, _ = http("GET", self.base + "/api/state")
        self.assertEqual(status, 200)
        state = data["state"]
        self.assertIn("connected", state)
        self.assertFalse(state["connected"])
        self.assertIn("status", state)

    def test_unknown_route_404(self):
        status, data, _ = http("GET", self.base + "/api/nope")
        self.assertEqual(status, 404)

    def test_bad_json_body(self):
        req = urllib.request.Request(
            self.base + "/api/refresh", data=b"{not json",
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            _OPENER.open(req, timeout=10)
            self.fail("expected 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)


# ------------------------------------------------------------------ 安全闸门
class TestConfirmGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = ServerFixture().start()
        cls.base = cls.fx.base

    @classmethod
    def tearDownClass(cls):
        cls.fx.stop()

    def test_motion_without_confirm_rejected(self):
        cases = [
            ("/api/move", {"joint": 3, "mode": "abs", "value": 10}),
            ("/api/servo/stop", {"joint": 3}),
            ("/api/j1/rotate", {"direction": "a", "seconds": 1}),
            ("/api/j2/pulse", {"direction": "up", "seconds": 1}),
            ("/api/gripper", {"action": "open"}),
            ("/api/estop", {}),
        ]
        for path, body in cases:
            with self.subTest(path=path):
                status, data, _ = http("POST", self.base + path, body)
                self.assertEqual(status, 428)
                self.assertIn("confirm", data["error"])

    def test_readonly_endpoints_do_not_need_confirm(self):
        for path, body in (("/api/refresh", {}), ("/api/verify", {}),
                           ("/api/read", {"slave": 3, "start": 0, "count": 4})):
            with self.subTest(path=path):
                status, data, _ = http("POST", self.base + path, body)
                self.assertNotEqual(status, 428)

    def test_disconnect_has_no_confirm_requirement(self):
        status, _, _ = http("POST", self.base + "/api/disconnect", {})
        self.assertNotEqual(status, 428)


# ------------------------------------------------------------------ 控制器
class TestController(unittest.TestCase):
    def setUp(self):
        self._orig = controller_mod.RobotArm
        controller_mod.RobotArm = fake_robot_arm_factory
        self.ctl = ArmController(default_port="COM_TEST")

    def tearDown(self):
        self.ctl.shutdown()
        controller_mod.RobotArm = self._orig

    def test_not_connected_jobs_fail_cleanly(self):
        out = self.ctl.refresh()
        self.assertFalse(out["ok"])
        self.assertIn("not connected", out["error"])

    def test_connect_refresh_and_move(self):
        out = self.ctl.connect("COM_TEST", 115200)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["result"]["online"], 6)

        out = self.ctl.refresh()
        self.assertTrue(out["ok"])
        self.assertTrue(out["result"]["j3"]["online"])

        # 关节 5 上电未使能：SDK 应自动 prepare
        out = self.ctl.servo_move(5, mode="abs", value=90.0, speed=200)
        self.assertTrue(out["ok"], out)
        self.assertAlmostEqual(out["result"]["pos"], 380, delta=12)

        # 软限位拦截（J3 范围 ±1100 counts）
        out = self.ctl.servo_move(3, mode="abs", value=500.0)
        self.assertFalse(out["ok"])
        self.assertIn("REFUSED", out["error"])

    def test_j1_and_j2_paths(self):
        self.ctl.connect("COM_TEST", 115200)
        out = self.ctl.j1_rotate("a", 2.5)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["result"]["zone"], 90)
        out = self.ctl.j2_pulse("up", 2.0)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["result"]["zone"], 90)
        out = self.ctl.gripper("open")
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["result"]["is_open"])

    def test_estop_stops_everything(self):
        self.ctl.connect("COM_TEST", 115200)
        out = self.ctl.estop()
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["result"], {"j1": True, "j2": True, "j3": True,
                                        "j4": True, "j5": True, "j6": True})

    def test_raw_read(self):
        self.ctl.connect("COM_TEST", 115200)
        out = self.ctl.raw_read(3, 0x19, 4)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["result"]["joint"], "J3")
        self.assertEqual(out["result"]["words"][2], 0x67C6)   # reg0x1B

    def test_events_and_log_ring(self):
        q = self.ctl.subscribe()
        self.ctl.log("info", "hello world")
        ev = q.get(timeout=2)
        self.assertEqual(ev["type"], "log")
        self.assertEqual(ev["message"], "hello world")
        self.assertEqual(self.ctl.recent_logs(1)[-1]["message"], "hello world")
        self.ctl.unsubscribe(q)

    def test_state_reflects_busy_and_queue(self):
        self.ctl.connect("COM_TEST", 115200)
        state = self.ctl.state()
        self.assertTrue(state["connected"])
        self.assertIn("busy", state)
        self.assertIn("queued", state)

    def test_set_servo_limits_restricts_moves(self):
        self.ctl.connect("COM_TEST", 115200)
        out = self.ctl.set_servo_limits(3, -500, 500)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["result"]["limits"], [-500, 500])
        # 限位后的度数要按关节自己的换算（含零位偏移）算
        self.assertAlmostEqual(out["result"]["limits_deg"][1],
                               self.ctl._arm[3].counts_to_deg(500), places=1)

        # 收紧后，原来合法的 60° 应该被拒（60/0.09 = 667 count > 500）
        out = self.ctl.servo_move(3, mode="abs", value=60.0)
        self.assertFalse(out["ok"])
        self.assertIn("REFUSED", out["error"])

        # 放宽回来，同一个目标就合法了
        self.assertTrue(self.ctl.set_servo_limits(3, -1100, 1100)["ok"])
        self.assertTrue(self.ctl.servo_move(3, mode="abs", value=60.0)["ok"])

    def test_set_servo_limits_rejects_non_servo(self):
        self.ctl.connect("COM_TEST", 115200)
        out = self.ctl.set_servo_limits(1, -10, 10)
        self.assertFalse(out["ok"])
        self.assertIn("not a servo", out["error"])

    def test_disconnect_clears_connection(self):
        self.ctl.connect("COM_TEST", 115200)
        out = self.ctl.disconnect()
        self.assertTrue(out["ok"], out)
        self.assertFalse(self.ctl.state()["connected"])
        self.assertFalse(self.ctl.refresh()["ok"])

    def test_dry_run_records_planned_writes(self):
        self.ctl.connect("COM_TEST", 115200, dry_run=True)
        out = self.ctl.servo_move(3, mode="rel", value=5.0)
        self.assertTrue(out["ok"], out)
        # dry-run 下 SDK 不会真的写，目标应留在待发列表里
        arm = self.ctl._arm
        self.assertTrue(arm.transport.dry_run)
        self.assertTrue(any(reg == 0x0B for _, reg, _ in arm.transport.planned_writes))


# ------------------------------------------------------------------ SSE
class TestSSE(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = ServerFixture().start()
        cls.base = cls.fx.base

    @classmethod
    def tearDownClass(cls):
        cls.fx.stop()

    def test_event_stream_headers_and_first_frames(self):
        req = urllib.request.Request(self.base + "/api/events")
        resp = _OPENER.open(req, timeout=10)
        try:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/event-stream", resp.headers.get("Content-Type", ""))
            # 第一批应至少含一条 state 事件（历史日志可能为空）
            buf = ""
            deadline = time.time() + 8
            while time.time() < deadline:
                chunk = resp.readline()
                if not chunk:
                    break
                buf += chunk.decode("utf-8", "replace")
                if '"type": "state"' in buf or '"type":"state"' in buf:
                    break
            self.assertIn("data:", buf)
            self.assertIn("state", buf)
        finally:
            resp.close()

    def test_log_propagates_to_subscriber(self):
        q = self.fx.controller.subscribe()
        try:
            self.fx.controller.log("warn", "unit-test-marker")
            deadline = time.time() + 3
            found = None
            while time.time() < deadline:
                try:
                    ev = q.get(timeout=1)
                except Exception:
                    break
                if ev.get("message") == "unit-test-marker":
                    found = ev
                    break
            self.assertIsNotNone(found, "subscriber did not receive the log event")
            self.assertEqual(found["level"], "warn")
        finally:
            self.fx.controller.unsubscribe(q)


class TestFrontendWiring(unittest.TestCase):
    """没有浏览器也能抓到的前端错（少写/拼错 DOM id、引用了不存在的资源）。"""

    STATIC = os.path.join(ROOT, "webui", "static")
    #: 由 JS 动态创建的 class，不必出现在 HTML 里
    JS_CREATED_CLASSES = {
        "jcard", "jlabel", "log-line", "trace-line", "toast",
        "reg-cell", "reg-grid", "mrow", "msec", "jbar-fill", "jbar-mark",
    }
    #: 由 JS 拼接出来的 id
    DYNAMIC_IDS = {"tab-status", "tab-log", "tab-debug", "tab-model"}

    def _read(self, name):
        with open(os.path.join(self.STATIC, name), encoding="utf-8") as fh:
            return fh.read()

    def test_every_dom_id_used_by_app_js_exists(self):
        html = self._read("index.html")
        app = self._read("app.js")
        ids = set(re.findall(r'\bid="([^"]+)"', html))
        # $('id') 与 $$('id') 都会匹配，后者拿到的是选择器，过滤掉
        used = set(m.group(1) for m in re.finditer(r"\$\$?\('([^']+)'\)", app))
        used = set(u for u in used if u and u[0] not in ".#[")
        missing = sorted(used - ids - self.DYNAMIC_IDS)
        self.assertEqual(missing, [], "app.js 引用了 HTML 里不存在的 id: %s" % missing)

    def test_every_class_selector_exists(self):
        html = self._read("index.html")
        app = self._read("app.js")
        classes = set()
        for m in re.findall(r'class="([^"]+)"', html):
            classes |= set(m.split())
        used = set(re.findall(r"\$\$?\('\.([a-zA-Z0-9_-]+)", app))
        missing = sorted(used - classes - self.JS_CREATED_CLASSES)
        self.assertEqual(missing, [], "引用了未定义的 class: %s" % missing)

    def test_html_references_existing_assets(self):
        html = self._read("index.html")
        refs = re.findall(r'(?:src|href)="/static/([^"]+)"', html)
        self.assertTrue(refs)
        for ref in refs:
            with self.subTest(asset=ref):
                self.assertTrue(os.path.isfile(os.path.join(self.STATIC, ref)),
                                "缺少静态资源 %s" % ref)

    def test_app_js_binds_every_handler_element(self):
        """标签页按钮、视图预设等控件的 data-* 属性要齐全。"""
        html = self._read("index.html")
        self.assertEqual(len(re.findall(r'class="tab[ "]', html)), 4)
        self.assertEqual(len(re.findall(r'data-view="', html)), 5)
        for name in ("chk-grid", "chk-axes", "chk-markers", "chk-trail",
                     "chk-outline", "chk-fog", "chk-ghost", "chk-trace",
                     "chk-auto-refresh", "chk-dryrun",
                     "ghost-readout", "ghost-warn", "dryrun-banner",
                     "btn-leave-dryrun", "link-readout", "chk-links",
                     "btn-model-save", "btn-model-import", "btn-model-export",
                     "btn-model-reset", "model-file", "model-path-note"):
            with self.subTest(control=name):
                self.assertIn('id="%s"' % name, html)

    def test_js_syntax(self):
        """有 node 就顺便做语法检查（没有则跳过）。"""
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node 不可用")
        for name in ("mat4.js", "model.js", "viewer.js", "app.js"):
            with self.subTest(file=name):
                proc = subprocess.run([node, "--check", os.path.join(self.STATIC, name)],
                                      capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0,
                                 "%s 语法错误:\n%s" % (name, proc.stderr))


class TestModelStore(unittest.TestCase):
    """模型标定的持久化（保存 / 导入 / 重置 / 校验）。"""

    def setUp(self):
        import tempfile
        self._orig_path = model_store.OVERRIDE_PATH
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)                       # 从一开始就不存在
        model_store.OVERRIDE_PATH = path
        self.path = path

    def tearDown(self):
        model_store.OVERRIDE_PATH = self._orig_path
        if os.path.isfile(self.path):
            os.remove(self.path)
        for leftover in (self.path + ".tmp",):
            if os.path.isfile(leftover):
                os.remove(leftover)

    def _model(self):
        return model_store.merged_model()

    def test_factory_defaults_when_no_override_file(self):
        m = self._model()
        self.assertFalse(m["saved"]["exists"])
        self.assertEqual(m["saved"]["path"], self.path)
        # 出厂值：J3/J4 都是 180（传感器 0 = 连杆完全折回的收拢姿态）
        self.assertEqual(next(j for j in m["joints"] if j["id"] == 3)["home_deg"], 180.0)
        self.assertEqual(next(j for j in m["joints"] if j["id"] == 4)["home_deg"], 180.0)

    def test_config_is_an_isolated_copy(self):
        """回归：model_config() 必须返回深拷贝。

        否则在它上面合并覆盖值会把模块级出厂数据一起改掉，
        「重置」就永远回不到出厂值。
        """
        a = model_config.model_config()
        a["chain"][4]["pivot"][0] = 999
        a["joints"][3]["home_deg"] = 123
        b = model_config.model_config()
        self.assertNotEqual(b["chain"][4]["pivot"][0], 999)
        self.assertNotEqual(b["joints"][3]["home_deg"], 123)
        # 模块级出厂数据也不能被污染
        self.assertNotEqual(
            next(s for s in model_config.ARM_CHAIN if s["joint"] == 4)["pivot"][0], 999)
        self.assertNotEqual(
            next(j for j in model_config.JOINTS if j["id"] == 4)["home_deg"], 123)

    def test_save_then_read_back(self):
        m = self._model()
        m["joints"][3]["home_deg"] = 12.5              # J4
        m["chain"][3]["pivot"] = [170.0, 0.0, 0.0]     # J3 连杆长度
        m["jaw"]["open_mm"] = 50.0
        saved = model_store.save(m)
        self.assertIn("saved_at", saved)
        self.assertTrue(os.path.isfile(self.path))

        again = self._model()
        self.assertTrue(again["saved"]["exists"])
        self.assertEqual(next(j for j in again["joints"] if j["id"] == 4)["home_deg"], 12.5)
        self.assertEqual(again["chain"][3]["pivot"][0], 170.0)
        self.assertEqual(again["jaw"]["open_mm"], 50.0)

    def test_reset_restores_factory_values(self):
        m = self._model()
        m["joints"][3]["home_deg"] = 33.0
        model_store.save(m)
        self.assertTrue(model_store.info()["exists"])

        self.assertTrue(model_store.clear())
        after = self._model()
        self.assertFalse(after["saved"]["exists"])
        self.assertEqual(next(j for j in after["joints"] if j["id"] == 4)["home_deg"], 180.0)
        self.assertFalse(model_store.clear(), "第二次 clear 应返回 False")

    def test_validation_rejects_bad_input(self):
        base = model_config.model_config()
        cases = [
            ("段数不符", {"chain": base["chain"][:3], "joints": base["joints"]}),
            ("joint 顺序变了", None),
            ("零向量轴", None),
            ("home 超范围", None),
            ("夹爪反向", None),
            ("软限位反了", None),
        ]
        # joint 顺序变了
        m = json.loads(json.dumps(base)); m["chain"][2]["joint"] = 9
        cases[1] = ("joint 顺序变了", m)
        m = json.loads(json.dumps(base)); m["chain"][2]["axis"] = [0, 0, 0]
        cases[2] = ("零向量轴", m)
        m = json.loads(json.dumps(base)); m["joints"][2]["home_deg"] = 9999
        cases[3] = ("home 超范围", m)
        m = json.loads(json.dumps(base)); m["jaw"] = {"open_mm": 5, "closed_mm": 10}
        cases[4] = ("夹爪反向", m)
        m = json.loads(json.dumps(base)); m["joints"][2]["soft_limits_deg"] = [100, 0]
        cases[5] = ("软限位反了", m)

        for name, model in cases:
            with self.subTest(case=name):
                with self.assertRaises(model_store.ModelOverrideError):
                    model_store.extract(model)

    def test_soft_limits_are_persisted(self):
        m = self._model()
        for j in m["joints"]:
            if j["control"] == "servo" and j["id"] == 4:
                j["soft_limits_deg"] = [0.0, 120.0]
        model_store.save(m)
        # 覆盖值应优先于 SDK 默认值
        merged = self._model()
        j4 = next(j for j in merged["joints"] if j["id"] == 4)
        self.assertAlmostEqual(j4["soft_limits_deg"][1], 120.0, places=1)
        # 也应能推给 SDK
        limits = model_store.configured_limits()
        self.assertEqual(limits[4], (0, int(round(120.0 / j4["deg_per_count"]))))

    def test_apply_soft_limits_to_arm(self):
        m = self._model()
        for j in m["joints"]:
            if j["control"] == "servo" and j["id"] == 4:
                j["soft_limits_deg"] = [0.0, 100.0]
        model_store.save(m)

        arm = RobotArm(transport=FakeTransport())
        applied = model_store.apply_soft_limits(arm)
        self.assertIn("4", applied)
        self.assertEqual(arm[4].limits, tuple(applied["4"]))
        # 收紧后，原来的 180° 目标应该被拒
        from robot_arm import SafetyError
        with self.assertRaises(SafetyError):
            arm[4].move_to_deg(180.0)


class TestModelHTTP(unittest.TestCase):
    """模型保存/重置的 HTTP 接口。"""

    def setUp(self):
        import tempfile
        self._orig_path = model_store.OVERRIDE_PATH
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd); os.remove(path)
        model_store.OVERRIDE_PATH = path
        self.path = path
        self.fx = ServerFixture().start()
        self.base = self.fx.base

    def tearDown(self):
        self.fx.stop()
        model_store.OVERRIDE_PATH = self._orig_path
        for p in (self.path, self.path + ".tmp"):
            if os.path.isfile(p):
                os.remove(p)

    def test_get_model_reports_override_path(self):
        status, data, _ = http("GET", self.base + "/api/model")
        self.assertEqual(status, 200)
        saved = data["model"]["saved"]
        self.assertEqual(saved["path"], self.path)
        self.assertFalse(saved["exists"])

    def test_save_then_reset(self):
        status, data, _ = http("GET", self.base + "/api/model")
        model = data["model"]
        next(j for j in model["joints"] if j["id"] == 3)["home_deg"] = 7.5

        status, data, _ = http("POST", self.base + "/api/model/save",
                               {"model": model, "confirm": True})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["saved"]["home_deg"]["3"], 7.5)
        self.assertTrue(os.path.isfile(self.path))

        status, data, _ = http("GET", self.base + "/api/model")
        self.assertEqual(next(j for j in data["model"]["joints"] if j["id"] == 3)["home_deg"], 7.5)

        status, data, _ = http("POST", self.base + "/api/model/reset", {"confirm": True})
        self.assertEqual(status, 200)
        self.assertTrue(data["removed"])
        # 回到出厂值 180（不是我们刚保存的 7.5）
        self.assertEqual(next(j for j in data["model"]["joints"] if j["id"] == 3)["home_deg"], 180.0)

    def test_save_rejects_bad_model(self):
        status, data, _ = http("POST", self.base + "/api/model/save",
                               {"model": {"chain": [], "joints": []}, "confirm": True})
        self.assertEqual(status, 400)
        self.assertIn("模型不合法", data["error"])
        self.assertFalse(os.path.isfile(self.path), "校验失败时不应写文件")

    def test_save_requires_confirm(self):
        status, _, _ = http("POST", self.base + "/api/model/save", {"model": {}})
        self.assertEqual(status, 428)


class TestModelConfig(unittest.TestCase):
    def test_chain_matches_joints(self):
        cfg = model_config.model_config()
        joint_ids = sorted(j["id"] for j in cfg["joints"])
        self.assertEqual(joint_ids, [1, 2, 3, 4, 5, 6])
        chain_ids = sorted(s["joint"] for s in cfg["chain"] if s["joint"] is not None)
        self.assertEqual(chain_ids, joint_ids)

    def test_axes_are_unit_vectors(self):
        for seg in model_config.ARM_CHAIN:
            if seg["axis"] is None:
                continue
            self.assertEqual(len(seg["axis"]), 3)
            n = sum(v * v for v in seg["axis"]) ** 0.5
            self.assertAlmostEqual(n, 1.0, places=6,
                                   msg="seg %s axis not unit" % seg["label"])

    def test_json_serializable(self):
        text = json.dumps(model_config.model_config(), ensure_ascii=False)
        self.assertGreater(len(text), 500)

    def test_joint_by_id_and_slave(self):
        self.assertEqual(model_config.joint_by_id(3)["slave"], 3)
        self.assertEqual(set(model_config.JOINT_BY_SLAVE), {1, 2, 3, 4, 5, 6})

    def test_servo_soft_limits_come_from_the_sdk(self):
        """UI 的滑条范围必须就是 SDK 会接受的边界。

        否则用户能在预览里看到一个姿态、一点"移动"却被 SDK 拒绝，
        “预期姿态与实际重合”这个承诺就不成立了。
        """
        from robot_arm.registers import SERVO_CALIBRATION

        checked = 0
        for j in model_config.JOINTS:
            if j["control"] != "servo":
                self.assertIsNone(j.get("soft_limits_deg"),
                                  "%s 不是伺服关节，不应有 soft_limits_deg" % j["name"])
                continue
            with self.subTest(joint=j["name"]):
                sl = j.get("soft_limits_deg")
                self.assertIsNotNone(sl, "%s 缺少 soft_limits_deg" % j["name"])
                self.assertLess(sl[0], sl[1])
                calib = SERVO_CALIBRATION[j["slave"]]
                self.assertAlmostEqual(sl[0], calib.counts_to_deg(calib.limits[0]),
                                       places=2)
                self.assertAlmostEqual(sl[1], calib.counts_to_deg(calib.limits[1]),
                                       places=2)
                checked += 1
        self.assertEqual(checked, 3, "应该有 3 个伺服关节")


if __name__ == "__main__":
    unittest.main(verbosity=2)
