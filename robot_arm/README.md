# robot_arm —— 机械臂 Python SDK

对这块 6 轴机械臂本体固件的逆向 + 现场实测结果封装而成的 Python SDK。
寄存器语义、命令时序、安全约束的**证据来源**见仓库根目录《机械臂控制规格书.md》。

```python
from robot_arm import RobotArm

with RobotArm("COM8", verbose=True) as arm:
    arm.verify_all()              # 先校验固件身份（写命令前必须）
    print(arm.status())           # 只读快照

    arm.j2.up(2.5)                # 大臂抬升
    arm.j3.move_to_deg(-20)       # 肘部
    arm.j4.move_to_deg(10)        # 前臂
    arm.j5.move_to_deg(90)        # 腕部 roll（垂直）
    arm.j6.close()                # 夹爪
    arm.estop()                   # 关闭所有输出
```

---

## 1. 安装与依赖

* Python **3.9+**
* `pyserial`（唯一第三方依赖）

```bash
pip install pyserial
```

SDK 是纯源码包，把仓库根目录加入 `sys.path`（或 `pip install -e .`）即可：

```python
import sys; sys.path.insert(0, r"D:\Workspace\robot_arm_fw")
from robot_arm import RobotArm
```

硬件：USB-RS485 转换器，**COM8，115200 8N1**。

---

## 2. 目录结构

```
robot_arm/
├── __init__.py        公共 API 导出
├── exceptions.py      异常类型
├── protocol.py        Modbus RTU 帧构造/解析 + CRC16
├── registers.py       寄存器地址、固件身份表、标定数据
├── transport.py       RS485 传输层（重试 + 安全闸门 + dry-run）
├── arm.py             RobotArm 门面
├── cli.py             命令行
├── util.py            提示音 / 日志
└── joints/
    ├── base.py        BaseJoint（读寄存器 / 身份校验）
    ├── servo.py       ServoJoint  —— 关节 3 / 4 / 5（位置伺服）
    ├── yaw.py         YawJoint    —— 关节 1（底座回转，fw1）
    ├── lift.py        LiftJoint   —— 关节 2（丝杆升降）
    └── gripper.py     GripperJoint—— 关节 6（夹爪）
tests/test_offline.py  离线自测（不需要硬件）
examples/              示例脚本
```

---

## 3. 关节对象

| 属性 | 别名 | 从站 | 类型 | 位置反馈 |
|---|---|---|---|---|
| `arm.j1` | `arm.base` | 1 | `YawJoint` | 分区 0/45/90（≈ 90° 一档） |
| `arm.j2` | `arm.lift` | 2 | `LiftJoint` | 限位分区 0/45/90 |
| `arm.j3` | `arm.elbow` | 3 | `ServoJoint` | 计数，**0.09°/count** |
| `arm.j4` | `arm.forearm` | 4 | `ServoJoint` | 计数，**0.09°/count** |
| `arm.j5` | `arm.wrist` | 5 | `ServoJoint` | 计数，**0.237°/count** |
| `arm.j6` | `arm.gripper` | 6 | `GripperJoint` | 两态 0/45/90 |

`arm["j3"]`、`arm["elbow"]`、`arm[3]` 等价。

---

## 4. API 参考

### 4.1 `RobotArm`

```python
RobotArm(port="COM8", *, baudrate=115200, timeout=1.0, retries=5, retry_delay=0.25,
         dry_run=False, verbose=False, beep=False,
         servo_speeds={3: 300, ...}, servo_limits={3: (-1100, 1100), ...},
         transport=None)
```

| 方法 / 属性 | 说明 |
|---|---|
| `open()` / `close()` / `with` | 打开/关闭串口 |
| `verify_all(raise_on_error=True)` | 校验 6 个关节的固件身份 + `reg0x17`；返回 `{关节: {...}}` |
| `status()` | 只读快照，**永不抛异常**；断线关节返回 `{"online": False, "error": ...}` |
| `estop()` / `stop_all()` | 紧急停止：对所有关节下发"关闭输出"，逐个独立尝试 |
| `joints` | `[("j1", obj), ...]` |
| `transport` | 底层 :class:`ModbusTransport` |
| `beeper` | :class:`~robot_arm.util.Beeper`，`arm.beeper.mute()` 可静音 |

> **`verify_all()` 与 `status()` 都会读 36 个寄存器的长帧 —— 只能在关节静止时调用。**

### 4.2 `ServoJoint`（关节 3/4/5）

```python
joint.state()  -> ServoState          # pos / deg / err / target / enabled / blocked ...
joint.prepare()                        # 模式 + 速度 + 使能（关节 5 必须，模块会自动调）
joint.set_speed(300)                   # reg0x0A，范围 1..732
joint.enable() / stop()                # reg0x08 = 1 / 0
joint.move_to_deg(-20.0)               # 绝对角度（相对上电零点）
joint.move_to_counts(500)
joint.move_rel_deg(15.0)
joint.move_rel_counts(-100)
joint.set_limits(-800, 800)            # 软限位（计数）
joint.counts_to_deg(500) / deg_to_counts(45.0)
```

`move_*` 的关键字参数：`settle_tol=10`（到位容差，计数）、`timeout=30.0`（秒）、
`raise_on_block=True`。

判据说明：**主判据是位置偏差** ——
没到位 + 负载高 → `BlockedError`；没到位 + 负载正常 → `MotionTimeoutError`；
已到位但负载高 → 只记录不报错（`reg0x02` 运动刚结束时必然冲高）。
`ServoState.blocked` / `.load` 是那个负载指标的布尔/数值形式。

`ServoState` 字段：`vel / pos / err / mode / run / speed / target / fault`，
属性 `deg`、`target_deg`、`blocked`、`enabled`，方法 `as_dict()`。

### 4.3 `YawJoint`（关节 1）

```python
joint.state()  -> YawState             # zone / analog / direction / limit / speed_override
joint.start("a")                       # 设速度并启动（立即转）
joint.rotate("a", seconds=2.5)         # 转 2.5 秒后自动抑制；返回运动后的状态
joint.stop()                           # reg0x1F = 1（⚠ 同时撤掉力矩）
joint.set_speed_override(0x1482)
joint.clear_fault()                    # reg0x09 = 0x1234
```

方向可用 `"a"` / `"b"` / `"cw"` / `"ccw"` / `"up"` / `"down"`。
实测：`"a"` 使分区 45 → 90；`"b"` 使 45 → 0；2.5 s 脉冲约 90°。

速度覆盖只允许三个已验证值：`1`（抑制）、`0x1482`（常规）、`0x36B0`（高速）。
`rotate()` 默认 `max_pulse=5.0`，超长会抛 `SafetyError`。

### 4.4 `LiftJoint`（关节 2）

```python
joint.state()  -> LiftState            # zone(0/45/90) / motion / direction / run ...
joint.up(seconds=2.0)                  # 上行（安全方向）
joint.down(seconds=1.5)                # 下行脉冲（硬上限 2.5 s）
joint.run_until_limit(1, timeout=12)   # 一次跑到底靠限位停
joint.stop()                           # reg0x08 = 0
joint.enable()
```

⚠ **`down()` 的时长被硬限制在 `max_pulse`（默认 2.5 s）**：超过约 3.2 s 会触发固件
超速保护 → H 桥两路 100% 短接刹车 → 电流冲击 → **从站挂死，只能断电**。

`run_until_limit(DOWN)` 必须显式 `acknowledge_risk=True`（只有在确认下限位开关会被压下时才用）。

### 4.5 `GripperJoint`（关节 6）

```python
joint.state()  -> GripperState         # code 0=闭合 / 45=运动 / 90=张开
joint.open()                           # 张开 180°（约 4 s）
joint.close()                          # 闭合（约 1 s）
joint.stop()                           # reg0x08 = 0（⚠ 夹持力释放）
```

⚠ **没有夹取检测**：夹住物体与空夹的寄存器读数完全一致，无法判断钳口里是否有东西。

---

## 5. 安全机制（SDK 强制实施）

| 机制 | 说明 |
|---|---|
| **危险寄存器闸门** | `reg0x05`（`0xAA11` = 回零到 −2700、`0xA5A5` = 交替转速测试）在传输层被拦截，公有 API 无法写出 → `SafetyError` |
| **地址闸门** | 广播地址 `0`、维护地址 `0xAA` 一律禁止读写 |
| **复位魔法字闸门** | 往 `reg0x00` 写 `0xAA11` / `0xAA55` 被拦截 |
| **写前身份校验** | `verify()` / `verify_all()` 校验 `reg0x19/0x1B/0x1C` 与 `reg0x17`；不符抛 `IdentityError` |
| **软限位** | 伺服关节默认限制在实测行程内（3/4: ±1100 / ±850；5: [0, 681]），越界抛 `SafetyError` |
| **脉冲上限** | 关节 1 `rotate` ≤ `max_pulse`；关节 2 `down` ≤ 2.5 s |
| **禁轮询** | 所有运动统一"发命令 → `silent_wait()` → 单次读"；SDK 内部不会在运动中读寄存器 |
| **dry-run** | `RobotArm(dry_run=True)` 时写命令只记录，可用 `arm.transport.clear_planned_writes()` 取出 |

---

## 6. 单位与标定（★ 实测）

| 关节 | 换算 | 行程 | 软限位（计数，相对上电零点） |
|---|---|---|---|
| J3 | 1 count = **0.100897°** ★重标 | **收拢 = 0°，单向 0..250°** | `(1, 2479)` |
| J4 | 1 count = **0.100897°**（推定同 J3） | 收拢 = 0°，单向；远端未测 | `(8, 1500)` |
| J5 | 1 count = **0.237°**（377 count = 90°） | 681 count ≈ 161° | `(0, 681)` |
| J1 | `reg0x01` 分区 × 2° | 约 90°/2.5 s | 无 |
| J2 | 仅分区 | 约 90° | 无 |

⚠ **位置是"上电清零"的相对值**：换一次上电位置，所有软限位数值都要重新确认。

★ **2026-09-13 现场标定**：J3 现在以**完全收拢姿态为 0°**（上电即收拢），
换算率实测 **0.100897 deg/count**（旧值 0.09 偏小 11%），
零位偏移 J3=+1 count、J4=+8 count。标定工具：
`python analysis/encoder_calibration.py --joint 3 --deg 180`（只读）。
限位用**原始计数**存，所以换算率误差只影响"度"的显示，不会让机构跑过死点。
换个上电姿态后请重新测量行程，再用 `servo_limits={"j3": (lo, hi)}` 或 `joint.set_limits()` 覆盖。

---

## 7. 命令行

```bash
python -m robot_arm status                     # 只读快照
python -m robot_arm verify                     # 身份校验
python -m robot_arm estop                      # 紧急停止
python -m robot_arm read --slave 3 --start 0x00 --count 0x18

python -m robot_arm j1 rotate --dir a --seconds 2.5 --yes
python -m robot_arm j1 stop

python -m robot_arm j2 up   --seconds 2.0 --yes
python -m robot_arm j2 down --seconds 1.5 --yes

python -m robot_arm j3 move --rel-deg -20 --yes
python -m robot_arm j5 move --deg 90 --speed 200 --yes
python -m robot_arm j4 stop

python -m robot_arm j6 open  --yes
python -m robot_arm j6 close --yes
```

* `--port` / `--baud` / `--dry-run` / `--verbose` / `--beep` 放**子命令前后都可以**。
* 动作命令**必须加 `--yes`**，否则只是试运行。
* 退出码：`0` 成功，`1` 运行错误，`2` 被安全策略拒绝，`130` 用户中断（会自动尝试 estop）。

---

## 8. 示例脚本

```bash
python examples/read_all_status.py COM8                  # 只读
python examples/move_servo.py --joint 5 --deg 90         # 单关节运动
python examples/lower_joint2.py --status                 # J2 只读状态
python examples/lower_joint2.py --pulses 3 --seconds 1.5 # J2 逐脉冲下放
python examples/demo_sequence.py --list                  # 看有哪些步骤
python examples/demo_sequence.py --dry-run               # 看会下发什么（安全）
python examples/demo_sequence.py --only wrist --yes      # 只做一步
```

> ⚠ `lower_joint2.py` 是 J2 下行的**推荐方式**：J1 松垮导致限位开关不可信，
> 只能短脉冲 + 目测。脚本保证 Ctrl+C 会立即关输出（`reg0x08=0`）。

---

## 9. 离线自测

不需要硬件（内部用 `FakeTransport` 模拟寄存器页与固件行为）：

```bash
python tests/test_offline.py
```

36 个用例，覆盖：CRC 回归（对照规格书里已实测的帧）、安全闸门、身份校验、
伺服换算与软限位、关节 5 的 prepare 时序、关节 2 脉冲上限、夹爪/回转状态机、
dry-run、紧急停止、CLI 参数解析。

---

## 10. 与旧脚本的对应关系

`analysis/` 下的一次性脚本已被本 SDK 取代，但保留作为证据：

| 旧脚本 | SDK 等价 |
|---|---|
| `joint3.py --slave 3 move --rel-deg -20 --yes` | `python -m robot_arm j3 move --rel-deg -20 --yes` |
| `joint2.py up --seconds 2 --yes` | `python -m robot_arm j2 up --seconds 2 --yes` |
| `gripper.py open --yes` | `python -m robot_arm j6 open --yes` |
| `safe_rs485_probe.py ...` | `python -m robot_arm read --slave N --start X --count Y` |

SDK 修掉了旧脚本的几个问题：`--port` 只能前置、`args.yes` 在某些子命令不存在、
`build_write10` 在四个文件里各写一遍、dry-run 缺失。

---

## 11. 已知限制

1. **J1 无位置闭环** —— `reg0x01` 只有三档，定位靠"方向 + 时长"。
2. **J1 停止即失力矩** —— 关节变自由可反驱，这是设计特性。
3. **J2 无连续位置** —— 中间位置只能靠脉冲时长 + 目测。
4. **J6 无夹取检测** —— 无法从寄存器判断是否夹住。
5. **总线可靠性差** —— 单个通道可能挂死需断电；SDK 会抛 `TransportError` 而不是静默失败。
6. **位置为上电相对值** —— 软限位需按上电姿态重新确认。
7. **J5 越界写入的钳位行为不依赖** —— 固件的"钳位/回退"规则已实测，但 SDK 用软限位提前拦截，不依赖它。
