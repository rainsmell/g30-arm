"""离线自测：不需要真实硬件。

用 :class:`FakeTransport` 模拟六个从站的寄存器页与固件行为，验证：

* CRC 与已实测命令帧一致（回归测试）
* 安全闸门确实拦住了 ``reg0x05`` 等危险写
* 固件身份校验
* 位置伺服的目标换算与软限位
* 关节 2 的下行脉冲上限
* 夹爪 / 底座回转的状态机
* 紧急停止的下发内容

运行::

    python tests/test_offline.py
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from robot_arm import (  # noqa: E402
    IdentityError,
    RobotArm,
    SafetyError,
    TransportError,
    crc16_modbus,
)
from robot_arm.protocol import build_read_holding, build_write_multiple  # noqa: E402
from robot_arm.registers import IDENTITIES  # noqa: E402
from robot_arm.transport import ModbusTransport  # noqa: E402


class FakeTransport:
    """把六个从站模拟成一组内存寄存器页。"""

    def __init__(self, *, dry_run: bool = False):
        self.dry_run = dry_run
        self.is_open = True
        self.port = "FAKE"
        self.planned_writes: list[tuple[int, int, int]] = []
        self.banks = {slave: [0] * 0x24 for slave in range(1, 7)}
        self.writes: list[tuple[int, int, int]] = []
        self.slept = 0.0
        self.offline_slaves: set[int] = set()
        #: 这些从站写 0x0B 时位置不动（模拟卡住）
        self.stall_slaves: set[int] = set()
        for slave, ident in IDENTITIES.items():
            bank = self.banks[slave]
            bank[0x19] = ident.id_a
            bank[0x1B] = ident.build_lo
            bank[0x1C] = ident.build_hi
        # 位置伺服上电默认
        for slave in (3, 4):
            self.banks[slave][0x07] = 1
            self.banks[slave][0x08] = 1
            self.banks[slave][0x0A] = 732
        # 关节 5 上电未使能
        self.banks[5][0x07] = 0
        self.banks[5][0x08] = 0
        self.banks[5][0x0A] = 0
        # 关节 1 上电处于分区 45，已抑制
        self.banks[1][0x01] = 45
        self.banks[1][0x02] = 1
        self.banks[1][0x18] = 1
        self.banks[1][0x1F] = 1
        # 关节 2 上电在中间区
        self.banks[2][0x01] = 45
        self.banks[2][0x08] = 1
        # 夹爪闭合
        self.banks[6][0x01] = 0

    # ---- 传输层接口 ------------------------------------------------------
    def open(self):
        self.is_open = True
        return self

    def close(self):
        self.is_open = False

    def __enter__(self):
        return self.open()

    def __exit__(self, *_exc):
        self.close()

    def silent_wait(self, seconds: float) -> None:
        self.slept += max(0.0, seconds)

    def read_registers(self, slave, start, count, *, tries=None):
        if slave in self.offline_slaves:
            raise TransportError("simulated hung slave %d" % slave)
        if slave not in self.banks:
            raise TransportError("no such slave %d" % slave)
        return tuple(self.banks[slave][start:start + count])

    def try_read_registers(self, slave, start, count, *, tries=None):
        try:
            return self.read_registers(slave, start, count)
        except TransportError:
            return None

    def read_register(self, slave, reg, *, tries=None):
        return self.read_registers(slave, reg, 1)[0]

    def write_register(self, slave, reg, value, *, func=0x10, tries=None):
        ModbusTransport.guard_write(slave, reg, value)     # 复用真实的安全闸门
        value &= 0xFFFF
        if self.dry_run:
            self.planned_writes.append((slave, reg, value))
            return
        if slave in self.offline_slaves:   # 挂死的从站连写也不会应答
            raise TransportError("simulated hung slave %d" % slave)
        self.writes.append((slave, reg, value))
        self.banks[slave][reg] = value
        self._simulate(slave, reg, value)

    def clear_planned_writes(self):
        planned, self.planned_writes = self.planned_writes, []
        return planned

    # ---- 固件行为模拟 ----------------------------------------------------
    def _simulate(self, slave, reg, value):
        bank = self.banks[slave]
        if slave in (3, 4, 5) and reg == 0x0B:
            # 关节 5 未使能时写目标不动
            if slave == 5 and not (bank[0x07] == 1 and bank[0x08] == 1):
                return
            if slave in self.stall_slaves:
                return
            bank[0x01] = value if value < 0x8000 else value - 0x10000
        elif slave == 1 and reg == 0x0B:
            bank[0x01] = 90 if value == 0xAA11 else 0
        elif slave == 1 and reg == 0x1F:
            bank[0x02] = 1 if value == 1 else 0
        elif slave == 6 and reg == 0x0B:
            bank[0x01] = 90 if value else 0
        elif slave == 2 and reg == 0x0B:
            # 上行走到上限位，下行走到下限位
            bank[0x01] = 90 if value == 1 else 0


def make_arm(**kwargs) -> RobotArm:
    transport = FakeTransport(dry_run=kwargs.pop("dry_run", False))
    return RobotArm(transport=transport, **kwargs)


class TestProtocol(unittest.TestCase):
    """CRC 与帧构造的回归测试（对照规格书里已实测的帧）。"""

    KNOWN = [
        ("01 10 00 1F 00 01 02 14 82", "2B 5E"),
        ("01 10 00 0B 00 01 02 AA 11", "19 87"),
        ("01 10 00 0B 00 01 02 00 00", "A7 2B"),
        ("01 10 00 1F 00 01 02 00 01", "65 FF"),
        ("01 10 00 1F 00 01 02 00 00", "A4 3F"),
        ("01 06 00 09 12 34", "54 BF"),
        ("03 03 00 00 00 24", "44 33"),
        ("03 10 00 0A 00 01 02 00 FA", "3F D9"),
        ("03 10 00 0B 00 01 02 FE 0C", "FE 2E"),
        ("03 10 00 0B 00 01 02 01 F4", "BE 5C"),
        ("03 10 00 08 00 01 02 00 00", "BE 78"),
        ("02 10 00 08 00 01 02 00 00", "B3 E8"),
    ]

    def test_known_crcs(self):
        for body_hex, expected in self.KNOWN:
            with self.subTest(frame=body_hex):
                got = crc16_modbus(bytes.fromhex(body_hex.replace(" ", "")))
                self.assertEqual("%02X %02X" % (got & 0xFF, got >> 8), expected)

    def test_builders_match(self):
        self.assertEqual(
            build_write_multiple(1, 0x1F, [0x1482]).hex(" "),
            "01 10 00 1f 00 01 02 14 82 2b 5e",
        )
        self.assertEqual(build_read_holding(3, 0, 0x24).hex(" "),
                         "03 03 00 00 00 24 44 33")
        self.assertEqual(build_write_multiple(3, 0x0B, [0xFF9C]).hex(" "),
                         "03 10 00 0b 00 01 02 ff 9c ff d2")


class TestSafetyGates(unittest.TestCase):
    def test_reg05_blocked(self):
        with self.assertRaises(SafetyError):
            ModbusTransport.guard_write(3, 0x05, 0xAA11)

    def test_broadcast_blocked(self):
        with self.assertRaises(SafetyError):
            ModbusTransport.guard_write(0, 0x08, 0)

    def test_maintenance_address_blocked(self):
        with self.assertRaises(SafetyError):
            ModbusTransport.guard_write(0xAA, 0x05, 0xAA55)

    def test_reset_magic_blocked(self):
        with self.assertRaises(SafetyError):
            ModbusTransport.guard_write(1, 0x00, 0xAA55)

    def test_joint_api_cannot_reach_reg05(self):
        arm = make_arm()
        with self.assertRaises(SafetyError):
            arm.j3.write(0x05, 0xAA11)
        with self.assertRaises(SafetyError):
            arm.transport.write_register(5, 0x05, 0xA5A5)

    def test_ordinary_write_allowed(self):
        ModbusTransport.guard_write(3, 0x0B, 0xAA11)   # 目标值合法
        ModbusTransport.guard_write(1, 0x0B, 0xAA11)   # 方向值合法


class TestIdentity(unittest.TestCase):
    def test_verify_all_ok(self):
        arm = make_arm()
        results = arm.verify_all()
        self.assertTrue(all(r["ok"] for r in results.values()), results)
        self.assertEqual(len(results), 6)

    def test_identity_mismatch_raises(self):
        arm = make_arm()
        arm.transport.banks[4][0x1C] = 0xD81D      # 假装 4 号是别的固件
        with self.assertRaises(IdentityError):
            arm.j4.verify()

    def test_fault_raises(self):
        from robot_arm import JointFaultError

        arm = make_arm()
        arm.transport.banks[3][0x17] = 0xFC1A
        with self.assertRaises(JointFaultError):
            arm.j3.verify()

    def test_offline_slave_reported(self):
        arm = make_arm()
        arm.transport.offline_slaves.add(5)
        status = arm.status()
        self.assertFalse(status["j5"]["online"])
        self.assertTrue(status["j3"]["online"])


class TestServo(unittest.TestCase):
    def test_move_to_deg_converts_counts(self):
        arm = make_arm()
        joint = arm.j3
        state = joint.move_to_deg(45.0)
        expected = joint.deg_to_counts(45.0)          # 用关节自己的换算
        self.assertEqual(arm.transport.banks[3][0x0B], expected)
        self.assertAlmostEqual(state.pos, expected, delta=2)
        # 再钉一条：按 2026-09-13 标定（0.1009 deg/count，零位 1 count）
        self.assertEqual(expected, 1 + int(round(45.0 / 0.1009)))

    def test_move_rel_deg(self):
        arm = make_arm()
        joint = arm.j3
        joint.move_to_deg(10.0)
        p1 = arm.transport.banks[3][0x01]
        joint.move_rel_deg(20.0)
        p2 = arm.transport.banks[3][0x01]
        self.assertAlmostEqual(p2 - p1, int(round(20.0 / joint.deg_per_count)), delta=2)

    def test_encoder_calibration_matches_field_measurement(self):
        """把 2026-09-13 的现场标定结果钉死。

        实测：收拢姿态 0° = 1 count；转到 180° = 1785 count
              -> (1785-1)/180 = 9.9111 count/deg = 0.10090 deg/count

        旧假设 0.09 偏小 11%，会让 `move_to_deg(250)` 实际走到 280°，
        超过 270° 的底座干涉线 —— 所以这不只是个精度问题。
        """
        from robot_arm.registers import SERVO_CALIBRATION

        calib = SERVO_CALIBRATION[3]
        self.assertEqual(calib.zero_counts, 1)
        self.assertAlmostEqual(calib.deg_per_count, 180 / 1784, places=6)
        self.assertEqual(calib.deg_to_counts(180.0), 1785)
        self.assertAlmostEqual(calib.counts_to_deg(1785), 180.0, places=3)
        self.assertAlmostEqual(calib.counts_to_deg(calib.zero_counts), 0.0, places=6)
        # 上限必须留在 270° 干涉线以内
        self.assertLessEqual(calib.counts_to_deg(calib.limits[1]), 250.15,
                             "J3 上限 %s 已越过 270° 干涉线的安全余量" % (calib.limits,))

    def test_j4_calibration_matches_field_measurement(self):
        """J4 的现场标定（rev=4：0°=1，45°=+459，90°=+875 count）。

        实测量：45°->+460、90°->+876（含零点 1）。
        最小二乘拟合（过原点）：99405/10125 = 9.8178 count/deg。

        单看 90° 会得到 9.7222，但与 45° 点矛盾 —— 三点离散说明
        主要是目测误差，所以用拟合值。详见 registers.py 注释。
        """
        from robot_arm.registers import SERVO_CALIBRATION

        j4 = SERVO_CALIBRATION[4]
        j3 = SERVO_CALIBRATION[3]
        self.assertEqual(j4.zero_counts, 1)
        self.assertAlmostEqual(j4.deg_per_count, 10125 / 99405, places=6)
        self.assertAlmostEqual(j4.counts_to_deg(j4.zero_counts), 0.0, places=6)

        # 两个实测点都应落在目测误差内（<2°）
        self.assertLess(abs(j4.counts_to_deg(460) - 45.0), 2.0,
                        "45° 实测点偏离拟合线过多")
        self.assertLess(abs(j4.counts_to_deg(876) - 90.0), 1.5,
                        "90° 实测点偏离拟合线过多")

        # 拟合值应与 J3 接近（同款关节）
        self.assertLess(abs(j4.deg_per_count / j3.deg_per_count - 1.0), 0.03,
                        "J4/J3 换算率差异过大，可能量表有误")

        # 上限 = 180°（用户实测：J3 先摆到 15° 就不干涉）
        self.assertLessEqual(j4.counts_to_deg(j4.limits[1]), 180.1)
        self.assertAlmostEqual(j4.counts_to_deg(j4.limits[1]), 179.98, places=1)
        self.assertEqual(j4.deg_to_counts(180.0), 1768)

    def test_soft_limits_refused(self):
        """J3/J4 的行程是**单向**的（收拢姿态 = 0）。

        2026-09-13 实测定标：上电即收拢姿态，离开收拢后 pos 只往正方向增。
        所以“负角度”就是朝机械死点方向，必须被拒。
        """
        arm = make_arm()
        # 超过远端（2500 count = 225 deg）
        with self.assertRaises(SafetyError):
            arm.j3.move_to_deg(300.0)
        # 朝机械死点方向（行程是 [0, +N]，没有负方向）
        with self.assertRaises(SafetyError):
            arm.j3.move_to_deg(-10.0)
        # 边界内是允许的
        self.assertAlmostEqual(arm.j3.move_to_counts(2400).pos, 2400, delta=10)

    def test_joint5_requires_prepare(self):
        arm = make_arm()
        self.assertEqual(arm.transport.banks[5][0x08], 0)
        state = arm.j5.move_to_deg(90.0)
        self.assertEqual(arm.transport.banks[5][0x07], 1)
        self.assertEqual(arm.transport.banks[5][0x08], 1)
        self.assertAlmostEqual(state.pos, 380, delta=10)   # 90 / 0.237 = 380
        self.assertIn((5, 0x0B, 380), arm.transport.writes)

    def test_joint5_window(self):
        arm = make_arm()
        state = arm.j5.move_to_counts(681)
        self.assertEqual(state.pos, 681)
        with self.assertRaises(SafetyError):
            arm.j5.move_to_counts(700)

    def test_speed_validation(self):
        arm = make_arm()
        with self.assertRaises(ValueError):
            arm.j3.set_speed(0)
        with self.assertRaises(ValueError):
            arm.j3.set_speed(733)

    def test_state_exposes_load_alias(self):
        """reg0x02 的语义是负载/力矩，不是位置误差 —— 提供 `load` 别名。"""
        arm = make_arm()
        arm.transport.banks[3][0x02] = 714
        st = arm.j3.state()
        self.assertEqual(st.load, st.err)
        self.assertEqual(st.load, 714)
        self.assertIn("load=714", str(st))
        self.assertIn("load", st.as_dict())
        self.assertTrue(st.blocked)          # 指标偏高
        arm.transport.banks[3][0x02] = 0

    def test_stop_writes_disable(self):
        arm = make_arm()
        arm.j3.stop()
        self.assertIn((3, 0x08, 0), arm.transport.writes)

    def test_high_loop_error_on_target_is_not_a_failure(self):
        """回归：运动刚结束时 reg0x02 会冲高，不能因此把“已到位”误判为堆转。

        现场实例（2026-09-13）：J3 移到 15°，pos=149 / 目标 150（已到位），
        但 reg0x02=808 > 500，旧逻辑直接抛 BlockedError 中止了序列。
        """
        arm = make_arm()
        arm.transport.banks[3][0x02] = 808          # 模拟运动刚结束的冲高
        target = arm.j3.deg_to_counts(15.0)
        state = arm.j3.move_to_deg(15.0)            # 不应抛异常
        self.assertAlmostEqual(state.pos, target, delta=2)
        self.assertGreater(abs(state.err), 500)     # 指标仍如实报告
        self.assertTrue(state.blocked)

    def test_off_target_with_high_loop_error_reports_blocked(self):
        """真的没到位 + 高环误差 = 确实卡住了。"""
        from robot_arm import BlockedError

        arm = make_arm()
        arm.transport.stall_slaves.add(3)           # 写目标也不动
        arm.transport.banks[3][0x02] = 808
        with self.assertRaises(BlockedError):
            arm.j3.move_to_counts(500, timeout=0.3)

    def test_off_target_without_loop_error_reports_timeout(self):
        from robot_arm import MotionTimeoutError

        arm = make_arm()
        arm.transport.stall_slaves.add(3)
        arm.transport.banks[3][0x02] = 20           # 没有力矩异常
        with self.assertRaises(MotionTimeoutError):
            arm.j3.move_to_counts(500, timeout=0.3)


class TestYaw(unittest.TestCase):
    def test_rotate_direction_a_reaches_zone_90(self):
        arm = make_arm()
        arm.transport.banks[1][0x1F] = 0x1482
        state = arm.j1.rotate("a", 2.5)
        self.assertEqual(state.zone, 90)
        self.assertEqual(state.deg, 180.0)      # 1 单位 = 2 度
        self.assertIn((1, 0x0B, 0xAA11), arm.transport.writes)
        self.assertEqual(arm.transport.banks[1][0x1F], 1)   # 收尾必须抑制

    def test_rotate_direction_b(self):
        arm = make_arm()
        state = arm.j1.rotate("b", 1.0)
        self.assertEqual(state.zone, 0)

    def test_pulse_cap(self):
        arm = make_arm()
        with self.assertRaises(SafetyError):
            arm.j1.rotate("a", 30.0)

    def test_bad_direction(self):
        arm = make_arm()
        with self.assertRaises(ValueError):
            arm.j1.rotate("sideways", 1.0)

    def test_stop_removes_torque(self):
        arm = make_arm()
        arm.j1.stop()
        self.assertEqual(arm.transport.banks[1][0x1F], 1)


class TestLift(unittest.TestCase):
    def test_up_reaches_upper_zone(self):
        arm = make_arm()
        state = arm.j2.up(2.0)
        self.assertEqual(state.zone, 90)
        self.assertTrue(state.at_upper_limit)
        self.assertEqual(arm.transport.banks[2][0x08], 0)   # 脉冲后关输出

    def test_down_pulse_cap(self):
        arm = make_arm()
        with self.assertRaises(SafetyError):
            arm.j2.down(3.0)                # > max_pulse 2.5

    def test_run_until_limit_down_needs_ack(self):
        arm = make_arm()
        with self.assertRaises(SafetyError):
            arm.j2.run_until_limit(0, timeout=1.0)
        state = arm.j2.run_until_limit(0, timeout=1.0, acknowledge_risk=True)
        self.assertEqual(state.zone, 0)

    def test_zone_name(self):
        arm = make_arm()
        self.assertIn("MIDDLE", arm.j2.state().zone_name)


class TestGripperTiming(unittest.TestCase):
    """夹爪定时开合 —— reg0x01 只有三态，中间开度只能靠时长控制。"""

    def test_open_for_stops_after_the_given_time(self):
        arm = make_arm()
        j = arm.j6
        before = arm.transport.slept
        state = j.open_for(1.5)
        # 假串口的 silent_wait 是空操作，所以看它累计的等待时间
        self.assertGreaterEqual(arm.transport.slept - before, 1.5)
        # 关键动作：开始张开 + 到点写 reg0x08=0 停止
        self.assertIn((6, 0x0B, 90), arm.transport.writes)
        self.assertIn((6, 0x08, 0), arm.transport.writes)
        self.assertIsInstance(state.counts, int)

    def test_close_for_targets_zero(self):
        arm = make_arm()
        arm.j6.close_for(1.0)
        self.assertIn((6, 0x0B, 0), arm.transport.writes)
        self.assertIn((6, 0x08, 0), arm.transport.writes)

    def test_move_for_rejects_bad_input(self):
        arm = make_arm()
        with self.assertRaises(SafetyError):
            arm.j6.move_for("sideways", 1.0)
        with self.assertRaises(SafetyError):
            arm.j6.move_for("open", 0)
        with self.assertRaises(SafetyError):
            arm.j6.move_for("open", 999)

    def test_partial_ratio_maps_to_time(self):
        from robot_arm.joints.gripper import FULL_TRAVEL_SECONDS
        arm = make_arm()
        arm.j6.open_partial(0.5)
        # 半开 -> 约全行程时间的一半
        written = [v for (sl, r, v) in arm.transport.writes if sl == 6 and r == 0x0B]
        self.assertTrue(written)
        with self.assertRaises(SafetyError):
            arm.j6.open_partial(1.5)

    def test_state_exposes_opening_helpers(self):
        arm = make_arm()
        arm.transport.banks[6][0x01] = 90
        st = arm.j6.state()
        self.assertEqual(st.counts, 90)
        self.assertEqual(st.code, 90)               # 旧字段名兼容
        self.assertAlmostEqual(st.opening_deg, 180.0)
        self.assertAlmostEqual(st.opening_ratio, 1.0)
        self.assertTrue(st.is_open)
        arm.transport.banks[6][0x01] = 45
        st = arm.j6.state()
        self.assertFalse(st.is_open)
        self.assertFalse(st.is_closed)
        self.assertTrue(st.is_moving)               # 45 = 在途中


class TestGripper(unittest.TestCase):
    def test_open_close(self):
        arm = make_arm()
        self.assertTrue(arm.j6.close().is_closed)
        state = arm.j6.open()
        self.assertTrue(state.is_open)
        self.assertIn((6, 0x0B, 90), arm.transport.writes)

    def test_prepare_sets_mode_and_enable(self):
        arm = make_arm()
        arm.transport.banks[6][0x07] = 0
        arm.transport.banks[6][0x08] = 0
        arm.j6.open()
        self.assertEqual(arm.transport.banks[6][0x07], 1)
        self.assertEqual(arm.transport.banks[6][0x08], 1)


class TestDryRun(unittest.TestCase):
    def test_no_writes_are_sent(self):
        arm = make_arm(dry_run=True)
        arm.j3.move_to_deg(10.0)
        self.assertEqual(arm.transport.writes, [])
        planned = arm.transport.planned_writes
        self.assertTrue(any(reg == 0x0B for _, reg, _ in planned), planned)

    def test_status_is_still_readable(self):
        arm = make_arm(dry_run=True)
        self.assertTrue(arm.status()["j3"]["online"])


class TestEstop(unittest.TestCase):
    def test_estop_stops_every_joint(self):
        arm = make_arm()
        arm.j2.enable()
        results = arm.estop()
        self.assertTrue(all(results.values()), results)
        self.assertEqual(arm.transport.banks[1][0x1F], 1)
        for slave in (2, 3, 4, 5, 6):
            self.assertEqual(arm.transport.banks[slave][0x08], 0)

    def test_estop_tolerates_offline_joint(self):
        arm = make_arm()
        arm.transport.offline_slaves.add(4)
        results = arm.estop()
        self.assertFalse(results["j4"])
        self.assertTrue(results["j3"])


class TestCli(unittest.TestCase):
    def test_parser_accepts_documented_invocations(self):
        from robot_arm.cli import build_parser

        parser = build_parser()
        cases = [
            ["status"],
            ["verify"],
            ["estop"],
            ["read", "--slave", "3", "--start", "0x00", "--count", "0x18"],
            ["j1", "rotate", "--dir", "a", "--seconds", "2.5", "--yes"],
            ["j2", "up", "--seconds", "2", "--yes"],
            ["j3", "move", "--rel-deg", "-20", "--yes"],
            ["j5", "move", "--deg", "90", "--yes"],
            ["j4", "move", "--counts", "100"],
            ["j6", "open", "--yes"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                args = parser.parse_args(argv)
                self.assertIsNotNone(args.cmd)

    def test_motion_requires_yes(self):
        from robot_arm.cli import build_parser

        args = build_parser().parse_args(["j3", "move", "--deg", "10"])
        self.assertFalse(getattr(args, "yes", False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
