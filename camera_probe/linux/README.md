# Linux 侧快速上手（夹爪摄像头）

目标设备：`0c45:636b`（Sonix USB Camera），XU GUID `28F03370-6311-4A2E-BA2C-6890EB334016`，bUnitID=3。

---

## 0. 依赖

```bash
sudo apt update
sudo apt install -y build-essential git cmake pkg-config \
    libusb-1.0-0-dev v4l-utils ffmpeg usbutils
# libuvc：有的发行版有 libuvc-dev；没有就源码装
sudo apt install -y libuvc-dev || {
    git clone https://github.com/libuvc/libuvc.git
    cd libuvc && mkdir build && cd build
    cmake .. && make -j && sudo make install && sudo ldconfig
}
```

## 1. 编译 XU 工具

```bash
cd camera_probe/linux
gcc -O2 -o xu_tool xu_tool.c -luvc -lusb-1.0
# 或一键： ./build.sh
```

> 若编译报 `uvc_get_extension_units` 未定义（发行版自带的 libuvc 太旧），
> 两种解法：① 按上面源码装最新 libuvc；② 把 `cmd_info`/`cmd_led` 里的
> `uvc_get_extension_units()` 链表循环换成直接对 **unit=3** 循环即可：
> ```c
> for (int sel = 1; sel <= 32; sel++) { int len = uvc_get_ctrl_len(devh, 3, sel); ... }
> ```

---

## 2. 先看设备是否被 uvcvideo 正常接管

```bash
# 插上摄像头后
dmesg | tail -40 | grep -i -E 'uvc|usb'
lsusb -d 0c45:636b -v | tee descriptors_linux.txt     # 完整描述符（用于和 Windows 抓的对比）
v4l2-ctl --list-devices
```

关注 `dmesg` 里有没有 `uvcvideo: ...` 的 **warning / error**（例如 "Failed to query (GET_INFO) UVC control" 或拓扑相关）。
Windows 上这颗摄像头一旦开始取流就报 `ERROR_GEN_FAILURE`；Linux 的 `uvcvideo` 对描述符顺序更宽容，**这里很可能直接就能出图**。

## 3. 看格式 / 直接试取流

```bash
DEV=$(v4l2-ctl --list-devices | awk '/USB Camera/{getline; print $1}' | head -1)
echo "DEV=$DEV"

v4l2-ctl -d "$DEV" --list-formats-ext
v4l2-ctl -d "$DEV" --all

# 抓 30 帧到文件（最直接的"能不能出图"测试）
v4l2-ctl -d "$DEV" --set-fmt-video=width=640,height=480,pixelformat=MJPG \
         --stream-mmap --stream-count=30 --stream-to=/tmp/arm.mjpg
ls -l /tmp/arm.mjpg          # 明显 >0 就说明有数据流

# 或者用 ffmpeg 存一张图
ffmpeg -f v4l2 -input_format mjpeg -video_size 640x480 -i "$DEV" -frames:v 1 /tmp/arm.png

# 或者用 guvcview 直接看画面
sudo apt install -y guvcview && guvcview -d "$DEV"
```

- 如果 `--stream-to` 得到 0 字节 / 卡住 / dmesg 报错 → 设备侧确实有问题（硬件或固件）。
- 如果能出图 → 问题只在 Windows 的 UVC 栈。

## 4. 枚举 / 操作 Extension Unit（补光灯在这里）

### 方式 A：本仓库的 xu_tool（推荐，直连 XU）

```bash
./xu_tool info          # 打印 XU 列表 + 每个控制的 LEN/INFO/MIN/MAX/RES/DEF/CUR
./xu_tool get 3 1       # 读 unit=3 sel=1 的当前值
./xu_tool set 3 1 01    # 写 unit=3 sel=1 = 0x01
./xu_tool led           # 自动遍历所有可写控制并逐个试值（每步停 2 秒）——盯着补光灯看！
```

`xu_tool led` 输出的 `>>> STEP n ...` 就是第 n 次写入；记下**灯亮的那一步**，就能确定是哪个控制、什么值。
（我们已知 Windows 上只放行了 sel 1/2/3/4/5/20，Linux 应该能看到**全部 32 个**。）

### 方式 B：v4l2 控制（uvcvideo 会把 XU 控制注册成 V4L2 控制）

```bash
v4l2-ctl -d "$DEV" --list-ctrls-menus | sed -n '1,200p'
# XU 控制会以非标准名字出现（不同内核版本命名不同，可能是 "Extension Unit Control" 或带 GUID 的名字）
v4l2-ctl -d "$DEV" --set-ctrl "<name>=<value>"
```

### 方式 C：uvcdynctrl（libwebcam）

```bash
sudo apt install -y uvcdynctrl
uvcdynctrl -d video0 -l          # 列出全部控制（含 XU）
uvcdynctrl -d video0 -g "<name>" # 读
uvcdynctrl -d video0 -s "<name>" <value>
```

### 方式 D：libuvc 直接调（写代码时）

```c
uvc_get_ctrl_len(devh, 3, sel);                   /* GET_LEN */
uvc_get_ctrl(devh, 3, sel, buf, len, UVC_GET_INFO);/* 0x86 */
uvc_get_ctrl(devh, 3, sel, buf, len, UVC_GET_CUR); /* 0x81 */
uvc_set_ctrl(devh, 3, sel, buf, len);              /* SET_CUR */
```
`UVC_GET_INFO` 位图：bit0=支持 GET_CUR，bit1=支持 SET_CUR，bit2/3=当前被自动更新禁用。

## 5. 顺带验证描述符顺序

Windows 侧解析出的 VideoControl 描述符顺序是 **Header → Output → XU → Input → Processing**（依赖倒置）。
在 Linux 上看内核怎么处理：

```bash
dmesg | grep -i uvcvideo
# 期望：能看到 4 个 entity（camera terminal / processing unit / extension unit / output terminal）
cat /sys/class/video4linux/video*/name
# XU 描述符（如果有暴露）
for f in /sys/class/video4linux/video*/device/../*/; do echo "$f"; done 2>/dev/null | head
```

---

## 6. 判定树

| 现象 | 结论 |
|---|---|
| `v4l2-ctl` 能出图，XU 能列出 32 个控制 | 设备/固件没问题，**Windows UVC 栈的兼容性问题** |
| 能出图但某控制能点亮补光灯 | 记下 **unit / sel / value**，这就是补光灯控制 |
| 也不能出图，dmesg 报错 | 模块硬件/固件故障（可换机再试确认） |
| XU 只能列出少数控制 | 固件未实现 `GET_INFO/GET_LEN/GET_MIN/GET_MAX`，控制被内核/驱动禁用 |

把 `xu_tool info` 的完整输出、`dmesg | grep uvc`、以及**灯亮对应的 STEP** 回贴即可。
