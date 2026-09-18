# 运动过程寄存器记录脚本

脚本：

```text
analysis/profile_motion_registers.py
```

它按照已经验证过的流程控制一个关节：

1. 读取上电前状态；
2. 设置 `0x001F=0x1482`；
3. 收拢：`0x000B=0xAA11`，默认10秒；
4. 展开：`0x000B=0x0000`，默认15秒；
5. 最后写入 `0x001F=1`，恢复PWM安全抑制。

与之前脚本不同的是，收拢和展开阶段不会用一个10秒/15秒的长时间
`sleep`。默认每秒采样5次，并记录：

```text
0x0001, 0x0002, 0x0005, 0x0009, 0x000B,
0x0013 .. 0x0025
```

输出：

```text
analysis/motion_logs/<tag>.csv
analysis/motion_logs/<tag>.json
analysis/motion_logs/<tag>.png     # 只有指定 --plot 且安装matplotlib时
```

## 先做干运行检查

不会发送任何命令：

```powershell
python analysis\profile_motion_registers.py --slave 3
```

## 实际运行3号关节

确认机械结构安全后：

```powershell
python analysis\profile_motion_registers.py --slave 3 --run --beep --plot
```

## 实际运行4号关节

```powershell
python analysis\profile_motion_registers.py --slave 4 --run --beep --plot
```

脚本一次只操作一个从站，不会给其他关节发送运动命令。

## 调整采样频率

例如10 Hz：

```powershell
python analysis\profile_motion_registers.py --slave 3 --run --beep --plot --sample-hz 10
```

采样频率越高，总线读取次数越多；建议先使用默认5 Hz。

## 绘图依赖

当前Python环境已经安装 `pyserial`，但没有安装 `matplotlib`。不带
`--plot` 时CSV和JSON仍然会生成；如果需要图表，安装：

```powershell
python -m pip install matplotlib
```

安装后再次运行脚本并加上 `--plot`。

## 安全行为

- 不加 `--run` 时绝不会发送运动帧；
- 只允许指定从站3或4；
- `finally`中总是尝试发送对应从站的：

```text
<slave> 10 00 1F 00 01 02 00 01 <CRC>
```

- 不发送 `0x0005=0x22AA` 或 `AA 06 00 05 AA 55`；
- 如果中途按 `Ctrl+C`，也会尝试恢复PWM抑制；
- 记录每个命令的TX/RX、时间戳、读取错误和寄存器变化。
