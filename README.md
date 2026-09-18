# G30 六轴机械臂 — 固件逆向与控制

对一台 6 轴机械臂（GD32F103 关节驱动板，Modbus/RS485）的固件逆向、协议还原，
以及基于实测结果构建的 Python SDK 与 3D Web 控制台。

## 目录结构

| 路径 | 说明 |
|---|---|
| [`机械臂控制规格书.md`](机械臂控制规格书.md) | ★ **总览入口**：协议 + 6 关节接口 + 固件逆向 + 安全规范 |
| [`robot_arm/`](robot_arm/README.md) | ★ **Python SDK**：面向对象的关节接口 + CLI + 离线自测 |
| [`webui/`](webui/README.md) | ★ **3D Web 控制台**：数字孪生 + 关节控制 + 日志/Debug |
| [`analysis/`](analysis/README.md) | 逆向分析文档、脚本与实测数据（各关节控制权威结论） |
| [`examples/`](examples/) | SDK 使用示例 |
| [`tests/`](tests/) | 离线测试与 WebUI 测试 |
| [`tools/`](tools/) | 图标生成、桌面快捷方式等辅助脚本 |
| `gd32_firmware.bin` / `gd32_firmware_2.bin` | 关节驱动板固件（fw1 / fw2） |

## 快速开始

```bash
# 依赖
pip install pyserial

# 启动 3D Web 控制台（Windows 可直接双击 启动机械臂Web控制台.bat）
python -m webui --open

# 或使用 Python SDK / CLI
python -m robot_arm --help
```

> ⚠️ **安全提示**：控制真实机械臂前请阅读规格书中的安全规范，
> 特别是位置伺服关节（3/4/5）的软限位与禁止寄存器。

## 机械臂拓扑

| 关节 | 驱动板 | Modbus 地址 | 类型 |
|---|---|---|---|
| 1 | 单通道板 (fw1) | 1 | 直流 + 限位（底座回转） |
| 2 | 双通道板 (fw2) | 2 | 丝杆升降（H 桥 + 限位） |
| 3 | 双通道板 (fw2) | 3 | 三相 FOC 位置伺服 |
| 4 | 独立板 | 4 | 位置伺服 |
| 5 | 双通道板 | 5 | 位置伺服（腕部） |
| 6 | 双通道板 | 6 | 夹爪（0=闭合 / 90=180° 张开） |
