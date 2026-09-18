# analysis/ 目录索引与结论状态

> 更新：2026-09-13
> **任何"关节 3 / 关节 2"的控制问题，一律以 [`JOINT3_CONTROL.md`](JOINT3_CONTROL.md) 为准。**
> **★ 全系统总览请先看仓库根目录的 [`../机械臂控制规格书.md`](../机械臂控制规格书.md)**
> （规格书形式，汇总协议 + 6 个关节接口 + 固件逆向 + 安全规范 + 工具链）。
> **★ 实际开发请直接用 [`../robot_arm/`](../robot_arm/README.md) Python SDK**；
> **★ 需要图形界面就用 [`../webui/`](../webui/README.md) 3D Web 控制台**；
> 本目录下的 `joint2.py` / `joint3.py` / `gripper.py` 已被它们取代，仅作证据保留。

## ★ 当前有效结论

| 文档 | 内容 | 状态 |
|---|---|---|
| **`../机械臂控制规格书.md`** | **★ 全系统规格书**（协议/6 关节/固件逆向/安全/工具） | ★ **总览入口** |
| **`../robot_arm/`** | **★ Python SDK**（面向对象的关节接口 + CLI + 离线自测） | ★ **推荐使用** |
| **`../webui/`** | **★ 3D Web 控制台**（数字孪生 + 关节控制 + 日志/Debug） | ★ **推荐使用** |
| **`JOINT3_CONTROL.md`** | **位置伺服关节（3/4/5 号）实测寄存器表、角度换算、命令序列、安全** | ★ **权威（已实测）** |
| **`GRIPPER_CONTROL.md`** | **关节 6（夹爪）开/合接口** | ★ **权威（已实测）** |
| `gripper.py` | 夹爪模块（open/close/status，自动完成 mode/使能设置） | ★ 已测试通过 |
| **`JOINT2_CONTROL.md`** | **关节 2（丝杆升降）实测接口** | ★ **权威（已实测）**：底座装回后下行可干净停在限位、不再挂死 |
| `joint3.py` | 位置伺服关节（3/4/5 号）控制模块（软限位、禁止 reg0x05、发命令→静默→单读） | ★ 已测试通过（slave 3/4/5） |
| **`JOINT1_CONTROL.md`** | **关节 1（底座回转，fw1）实测接口**；⚠ 停止后无保持力矩 | ★ **权威（已实测）** |
| `joint2.py` | 关节 2 控制模块（脉冲式上/下行，硬性限制 ≤2.5 s 避免触发剎车挂死） | ★ 已测试通过 |
| `joint3_range_probe.md` | 关节 3 行程/限位探测原始数据 | 有效 |
| `gd32_firmware_2.bin`（仓库根） | 关节 2/3 板固件（三相 FOC + Modbus 从站 2/3） | 已确认 |
| `gd32_firmware.bin`（仓库根） | 关节 1 板固件（单通道直流，从站 1） | 已确认 |
| `joint2_3_fw2_control.md` | fw2 静态反汇编：运行框架、SysTick、通道处理函数 | 有效（静态），结论以 JOINT3_CONTROL 为准 |
| `gd32_firmware_2_analysis.md` | fw2 固件总体结构（bootloader/app、协议、6 页寄存器） | 有效（静态） |
| `joint5_6_dual_channel_findings_2026-09-12.md` | 关节 5/6 腕部板 | 有效，**优先级最低** |
| `motor_register_map.md`, `protocol_*.md`, `motion_semantics.md`, `speed_*.md`, `gpio_pwm_findings.md`, `adc_pwm_reason.md`, `stop_research.md`, `firmware_feature_summary.md` | **关节 1（fw1）**的寄存器/协议/电机 | 有效，仅适用于关节 1 |
| `motion_profile_logger.md` | 采样/记录工具说明 | 有效 |

## 已确认的机械臂拓扑

```
关节1          : 单通道板  Modbus 地址 1   fw1   直流 + 限位
关节2          : 双通道板  Modbus 地址 2   fw2   丝杆摆臂（直流 H 桥 + 限位开关）
关节3          : 双通道板  Modbus 地址 3   fw2   三相 FOC 位置伺服
关节4          : 独立板    Modbus 地址 4         位置伺服（与关节 3 同接口，构建号 0xD824）
关节5 + 关节6  : 双通道板  Modbus 地址 5/6        关节5 = 位置伺服（1 count≈0.237°，行程161°）
                                                     关节6 = 夹爪（0=闭合 / 90=180°张开）
```

## 已归档（❌ 结论已作废，勿参考）

`archive/` 下的文档全部基于**错误的"关节 3 是直流限位型"模型**，已被实测推翻：

- `live_test_slave3_*.md`（16 篇）：把 `reg0x0B` 当成"方向寄存器"，实际它是**绝对目标位置**；
  当时写 `0xAA11` 其实是把目标设成 43537 计数（=3920°），所以看起来像"撞限位/只能单向"。
- `live_test_slave4_*.md`、`live_profile_slave3_*.md`、`multi_slave_*.md`、
  `motion_range_observation_*.md`、`power_on_reference_position_assessment_*.md`、
  `stepper_vs_dc_assessment.md`：同一错误模型下的观察。

**为什么会错**：关节 3 与关节 1 的寄存器表长得像（都有 `0x0B`/`0x17`/`0x18`），
但 fw2 里 `0x0B` 是目标位置、关节 2/3 是 FOC 闭环，语义完全不同。

## 已清空

`motion_logs/`：历史采样 log 已全部删除（旧 log 基于错误模型，易造成干扰）。
新的采样结果请带日期重新生成。
