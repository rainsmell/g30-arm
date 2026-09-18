# 夹爪摄像头 / 补光灯 —— 完整交接文档

> 一份自包含的现状记录。目标是带到 Linux 主机上继续验证。
> 采集时间：2026-09-13（Windows 11 build 26200 / 同一台华为笔记本）
> 相关文件：`camera_probe/descriptors.txt`（完整描述符存档）、`camera_probe/linux/xu_tool.c`（Linux XU 工具）、`camera_probe/linux/README.md`（Linux 快速上手）

---

## 0. TL;DR

| 项目 | 状态 |
|---|---|
| 设备识别 | ✅ `USB\VID_0C45&PID_636B`（Sonix），标准 UVC，最高 1600×1200 MJPEG@30 |
| 完整描述符 | ✅ 已抓取存档（`descriptors.txt`），可完整解析 |
| UVC 扩展单元（补光灯所在） | ✅ 已定位：**GUID `28F03370-6311-4A2E-BA2C-6890EB334016`，bUnitID=3** |
| 补光灯控制 | ⬜ 未确定。Windows 只放行 5/32 个 XU 控制，且都不是明显开关 |
| 取流（Windows） | ❌ 失败：设格式正常，一 `Run()` 就 `ERROR_GEN_FAILURE` |
| 取流（Linux） | ⬜ **待验证**（`uvcvideo` 对描述符顺序更宽容，很可能直接可用） |
| 关键疑点 | ⚠ VideoControl 描述符**顺序倒置**（固件缺陷）；XU 声明 32 个控制但固件未实现初始化请求 |

---

## 1. 设备标识

```
Windows 路径 : USB\VID_0C45&PID_636B&MI_00\6&315a9f63&0&0000
复合设备     : USB\VID_0C45&PID_636B\SN0001   (usbccgp)
VID / PID    : 0x0C45 (Sonix) / 0x636B
bcdUSB       : 0x0200 (USB 2.0 High-Speed, 480 Mbps)
bDeviceClass : 0xEF / 0x02 / 0x01  (IAD)
bcdDevice    : 0x0100
bNumConfigurations : 1
配置         : wTotalLength=592, bNumInterfaces=2, bMaxPower=500mA
单一视频功能 : 接口0=VideoControl(0x0E/0x01)  接口1=VideoStreaming(0x0E/0x02)
无独立 HID / 厂商接口 —— 补光灯只可能在 XU 里
```

**端点**
- 接口0：`EP 0x83 IN`，Interrupt，`wMaxPacketSize=16`，`bInterval=6`（UVC 状态中断端点）
- 接口1：`EP 0x81 IN`，Isochronous，alt1..6 分别 `128 / 256 / 800 / 2848 / 4896 / 5120` 字节/微帧

**支持的格式**（帧间隔均为离散值 `0.033/0.04/0.05/0.067/0.1/0.2 s`）

| 格式 | 分辨率 | 默认帧率 | maxFrameSize |
|---|---|---|---|
| MJPEG | 1600×900 | 30 | 2 880 589 |
| MJPEG | 1600×1200 | 30 | 3 840 589 |
| MJPEG | 640×480 | 30 | 614 989 |
| MJPEG | 1600×900（重复） | 30 | 2 880 589 |
| YUY2 | 1600×1200 | 5 | 3 840 000 |
| YUY2 | 640×480 | 30 | 614 400 |
| YUY2 | 1600×1200（重复） | 5 | 3 840 000 |

> 注：MJPEG 帧描述的 `dwMaxBitRate` 填的是**未压缩 YUY2 的码率**（1600×900×2×8×30 = 691 200 000），是固件偷懒，不影响使用。

---

## 2. ⚠ UVC 拓扑与描述符顺序缺陷

VideoControl 接口的类特定描述符（原始 hex，80 字节）：

```
0d240100015000c0e1e4000101
092403040101000300
1d2406037033f02811632e4aba2c6890eb334016200102049f070f0700
1224020101020000000000000000030e0000
0b240502010000027f1700
```

解析结果与**顺序**：

| # | 描述符 | ID | 关键字段 |
|---|---|---|---|
| 1 | `VC_HEADER` | — | bcdUVC=0x0100, totalLen=0x0050, bInCollection=1, iface=1 |
| 2 | `VC_OUTPUT_TERMINAL` | **4** | type=0x0101, **source=3** |
| 3 | `VC_EXTENSION_UNIT` | **3** | **GUID=28F03370-6311-4A2E-BA2C-6890EB334016**, bNumControls=32, bNrInPins=1, **source=2**, bControlSize=4, **bmControls=9F070F07** |
| 4 | `VC_INPUT_TERMINAL` | **1** | ITT_CAMERA(0x0201), bControlSize=3, bmControls=0x00000E |
| 5 | `VC_PROCESSING_UNIT` | **2** | **source=1**, bControlSize=2, bmControls=0x177F |

**逻辑拓扑**（依赖关系是对的，但描述符出现顺序是反的）：

```
Camera Terminal(1) --> Processing Unit(2) --> Extension Unit(3) --> Output Terminal(4)
```

**问题**：UVC 规范要求被引用的单元先出现。这里 Output(引用3) → XU(引用2) → Input → Processing(引用1) 全部倒置。
对照本机内建摄像头（`VID_3408&PID_C200`，同机取流正常）顺序是规范的 `Header → Input → Processing → Output → XU`。
Windows 的 `usbvideo.sys` 因此没能正确注册 Processing Unit / XU 的部分控制。

**待验证**：Linux `uvcvideo` 按 ID 解析、不依赖出现顺序，理论上能正确处理——这正是要去 Linux 试的原因之一。

---

## 3. Extension Unit（补光灯唯一可能的载体）

```
GUID        : 28F03370-6311-4A2E-BA2C-6890EB334016
            （描述符字节 le: 70 33 f0 28 11 63 2e 4a ba 2c 68 90 eb 33 40 16）
bUnitID     : 3
bNumControls: 32
bNrInPins   : 1   (source = Processing Unit 2)
bControlSize: 4
bmControls  : 9F 07 0F 07
```

`bmControls` 位图（bit0 = 控制1）声明支持的控制：

```
控制 1,2,3,4,5, 8,9,10,11, 17,18,19,20, 25,26,27    （共 16 个）
控制 6,7 / 12-16 / 21-24 / 28-32 未声明
```

### 3.1 Windows 上实际可访问的（仅 5 个）

Windows 在设备初始化时对每个控制发 `GET_INFO/GET_LEN/GET_MIN/GET_MAX`，**任一失败即禁用该控制**。
结果只有下列控制可用（重插 USB 后多出 sel=5）：

| sel | GET 长度 | 观测值（hex） | SET 可行 | 形态判断 |
|---|---|---|---|---|
| 1 | 4 | `01 00 00 00`（多次连读会自增：`03,04,05..0b`） | ✅ | 状态/计数器 |
| 2 | 8 | `20 00 4b 4b 00 00 00 00` | 时好时坏 | 不确定 |
| 3 | 11 | `00 00 f0 4b 00 00 00 00 00 00 00` | ✅ | 状态寄存器 |
| 4 | 11 | `00 00 00 4b 00 00 00 00 00 00 00` | ✅ | 状态寄存器 |
| 5 | 11 | `2d 04 01 4b 00 00 00 00 00 00 00` | ? | 状态寄存器 |
| 20 | 26 | `00…00 04 04 00 00 00 00 00 80 00 80 a7 89 0e 36` | ❌ `ERROR_GEN_FAILURE` | 只读信息块 |

> 重插 USB **之前**，同一批位置的字节是 `4f`（如 `20 00 4f 4f …`）；重插后变成 `4b`。说明这些值随设备状态变化。
> 控制 6..19、21..32 全部返回 `ERROR_NOT_FOUND (0x80070490)` —— 被 Windows 禁用。

**这 5 个都不像补光灯开关**（都是多字节状态/信息）。补光灯大概率在**被禁用**的那批里 → Linux 上应能看到全部 32 个。（注意：声明了 16 个，但 Windows 只放行 5 个；第一个断档在控制 6，按微软文档“断档之后的控制不再暴露”，却又能看到控制 20，说明固件与驱动的交互很反常。）

### 3.2 标准 UVC 控件（对照，也已被枚举）

| 节点 | 属性集 | 可用属性 |
|---|---|---|
| node 2 | `PROPSETID_VIDCAP_CAMERACONTROL` | 曝光(4)、焦距(18)、自动曝光优先(19) |
| node 3 | `PROPSETID_VIDCAP_VIDEOPROCAMP` | 亮度(0)、对比度(1)、色相(2)、饱和度(3)、锐度(4)、伽马(5)、白平衡(7,8)、背光补偿(9)、增益(13) |

**没有任何"LED/补光/照明"标准控件。** 补光灯只能在 XU 中。

---

## 4. Windows 侧取流失败（记录，供对照）

| 测试 | 结果 |
|---|---|
| OpenCV MSMF / DSHOW（index 1） | 打开成功，**0 帧**；MSMF 错误 `0xC00D3EA2`（"记录的设备不再存在"） |
| OpenCV 多分辨率多格式（320×240 … 1600×1200 × MJPG/YUY2） | 全部 **0 帧** |
| DirectShow 手动构图 | `set_format` 成功 → `run()` 返回 **`ERROR_GEN_FAILURE (0x8007001F)`** |
| ffmpeg dshow | `Could not RenderStream to connect pins` |

**已排除的因素**（都做过实验）：

| 假设 | 实验 | 结论 |
|---|---|---|
| 华为 `ccfltr.sys`（AI 摄像头过滤驱动，挂在摄像头类 `UpperFilters`）干扰 | 临时摘除并重启设备，驱动栈变为 `ksthunk, usbvideo, usbccgp` | ❌ 仍 0 帧（已还原） |
| 供电塌陷导致掉线复位 | 取流中轮询设备地址 | ❌ 地址恒为 1，未重新枚举 |
| Frame Server 状态异常 | 重启 `FrameServer` + 设备 disable/enable | ❌ 仍 0 帧 |
| 系统/权限/隐私设置 | 内建摄像头同 API 正常出图 | ❌ 不成立 |

**结论**：设备枚举/描述符/设格式均正常，但设备端拒绝开始流传输。指向**固件/硬件**层面的问题。

---

## 5. Linux 待办（重点）

工具已备好：`camera_probe/linux/xu_tool.c` + `README.md`。

```bash
# 依赖
sudo apt update && sudo apt install -y build-essential pkg-config libusb-1.0-0-dev \
     v4l-utils ffmpeg usbutils
sudo apt install -y libuvc-dev || {   # 源码装 libuvc
  git clone https://github.com/libuvc/libuvc.git && cd libuvc
  mkdir build && cd build && cmake .. && make -j && sudo make install && sudo ldconfig
}

# 1) 描述符 & 内核识别
lsusb -d 0c45:636b -v | tee descriptors_linux.txt
dmesg | grep -i uvc            # 看 uvcvideo 有没有报 warning/error
v4l2-ctl --list-devices
DEV=$(v4l2-ctl --list-devices | awk '/USB Camera/{getline; print $1}' | head -1)

# 2) 能不能出图（最关键）
v4l2-ctl -d "$DEV" --list-formats-ext
v4l2-ctl -d "$DEV" --set-fmt-video=width=640,height=480,pixelformat=MJPG \
         --stream-mmap --stream-count=30 --stream-to=/tmp/arm.mjpg
ls -l /tmp/arm.mjpg
ffmpeg -f v4l2 -input_format mjpeg -video_size 640x480 -i "$DEV" -frames:v 1 /tmp/arm.png

# 3) 枚举 XU 全部控制
cd camera_probe/linux && gcc -O2 -o xu_tool xu_tool.c -luvc -lusb-1.0
./xu_tool info          # 期望看到 unit=3，和 32 个控制

# 4) 找补光灯：自动遍历所有可写控制，每步停 2 秒
./xu_tool led           # 记下灯亮对应的 ">>> STEP n"
```

**要回贴的信息**
1. `./xu_tool info` 的完整输出（尤其 **unit=3 下有多少个 sel**）
2. `dmesg | grep -i uvc`
3. `v4l2-ctl ... --stream-to` 的字节数 / 是否出图
4. 补光灯点亮对应的 **STEP 编号**（或 `./xu_tool info` 里最像开关的那一行）

---

## 6. 附：Windows 下访问该 XU 的正确姿势（备查）

网上资料普遍写错，这里记录已验证可用的写法（`usbvideo.sys` + `IKsControl`）：

```c
KSP_NODE node;
node.Property.Set   = <XU 自身的 GUID>;   // 28F03370-...，不是某个固定 PROPSETID
node.Property.Id    = 控制号;              // 0 = INFO, 1..n = 各控制
node.Property.Flags = KSPROPERTY_TYPE_GET | KSPROPERTY_TYPE_TOPOLOGY;  // 0x10000001，缺 TOPOLOGY 会返回 ERROR_SET_NOT_FOUND
node.NodeId         = 1;                   // DirectShow 拓扑里 DEV_SPECIFIC 节点（KSPROPERTY_TOPOLOGY_NODES 得到）
node.Reserved       = 0;
pKsControl->KsProperty((PKSPROPERTY)&node, sizeof(node), buf, len, &ret);
```

（本仓库工具：`camera_probe/uvc_xu.py`，用法 `python uvc_xu.py info|list|get <id>|set <id> <hex>`。）

---

## 7. 工具与文件清单

| 路径 | 说明 |
|---|---|
| `camera_probe/descriptors.txt` | **完整 USB 描述符存档**（原始 hex + 解析） |
| `camera_probe/linux/xu_tool.c` | **Linux XU 工具**（libuvc）：`info / get / set / led / diag` |
| `camera_probe/linux/README.md` | Linux 快速上手 + 判定树 |
| `camera_probe/linux/build.sh` | 一键装依赖 + 编译 |
| `camera_probe/uvc_xu.py` | Windows XU 工具（已验证可用） |
| `camera_probe/uvc_full.py` | Windows 下用 libusb 解析完整 UVC 描述符 |
| `camera_probe/topo_nodes.py` | 读 DirectShow 拓扑（节点/连接） |
| `camera_probe/opencv_probe.py` / `deep_probe.py` | Windows 取流验证（均 0 帧） |
| `camera_probe/ccfltr_off.ps1` / `ccfltr_restore.ps1` | 临时摘除/恢复华为摄像头过滤驱动 |
| `analysis/CAMERA_MODULE.md` | **本文档** |

---

## 8. 未决问题

| # | 问题 | 优先级 |
|---|---|---|
| Q1 | Linux 下能否出图？（判断是 Windows 兼容性 vs 硬件故障） | 高 |
| Q2 | XU 的 32 个控制在 Linux 下能看到几个？ | 高 |
| Q3 | 补光灯是哪个 unit/sel、什么值点亮？ | 高 |
| Q4 | 描述符顺序倒置是否会导致 Windows 取流失败？ | 中 |
| Q5 | 该模块在 Windows 上是否需要特定 XU 指令才启动流？ | 中 |
| Q6 | SN9C2726 的官方/第三方 XU 控制语义文档是否存在？ | 低 |
