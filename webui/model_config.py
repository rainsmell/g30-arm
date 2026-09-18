"""机械臂的"数字孪生"参数：关节控制语义 + 3D 模型几何。

**这里是 3D 模型与控制面板的唯一数据源。** 前端不做任何硬编码，
它从 ``GET /api/model`` 读取本文件的内容。改这个文件就能改模型。

几何尺寸根据实物照片 ``arm.jpg`` 目测（比例近似，单位 mm）。
如果你量到更准确的尺寸，只改 ``ARM_CHAIN`` 即可，无需动前端。

运动学链（与照片对应）::

    安装板 ─┬─ J1 底座回转 (绕 Z)
            └─ 立柱
                └─ J2 丝杆升降 (绕 Y, 0..90 度，蓝胶带模块 = 大臂)
                    └─ J3 肘部 (大圆盘, 绕 Y)
                        └─ 细连杆
                            └─ J4 腕俯仰 (末端圆盘, 绕 Y)
                                └─ J5 腕部 (绕 X, 前臂长轴)
                                    └─ J6 夹爪 (直线开合)
"""

from __future__ import annotations

import copy
from typing import Dict, List

#: 软限位（SDK 实际会拒绝的边界）直接取自 SDK，保证 UI 永远不会让用户
#: 设一个“模型里能看到、但一发命令就被拒”的角度。
from robot_arm.registers import SERVO_CALIBRATION

#: 单位
UNITS = "mm"

#: 配色
#:
#: 注意：这些颜色会被渲染器当作 **sRGB 显示色**，在片元里先转线性空间做光照、
#: 输出前再编码回 sRGB。背景是深色 #161a20，所以材质整体偏亮灰，
#: 保证轮廓清晰。改暗会重新变回"看不清"。
COLORS = {
    "static": "#626b78",      # 安装底板
    "foot": "#4a525d",        # 地脚 / 底座下沿
    "column": "#7b8593",      # 立柱 / 关节壳体
    "arm": "#6b7482",         # 大臂
    "tape": "#2f8ff5",        # 蓝胶带（实物特征）
    "screw": "#c8d0da",       # 丝杆
    "carriage": "#525a65",    # 滑块
    "disc": "#5a636f",        # 关节目盘（照片里偏黑，这里提亮以保可读性）
    "rod": "#939daa",         # 连杆
    "wrist": "#737d8a",       # 腕部体
    "tool": "#a2abb6",        # 夹爪底座
    "jaw": "#d2d9e1",         # 夹爪片
}


def _feet(cx: float, cy: float, r: float, h: float, color: str) -> List[Dict]:
    """四角地脚。"""
    return [
        {"prim": "cylinder", "axis": "z", "at": [sx * cx, sy * cy, h / 2],
         "radius": r, "height": h, "color": color}
        for sx in (-1, 1) for sy in (-1, 1)
    ]


#: 运动链。每一段：
#:
#:   ``pivot``  本段关节轴心相对于**父段坐标系**的偏移
#:   ``axis``   旋转轴（父段坐标系，右手系）；``None`` 表示固定段
#:   ``parts``  本段图元，画在**本段旋转后的坐标系**里
#:   ``joint``  对应关节 id（用于控制面板联动与高亮）
#:
#: 递推：``M_i = M_(i-1) . T(pivot_i) . R(axis_i, theta_i)``
#:
#: 注意 pitch 关节的轴写成 ``[0, -1, 0]``（而不是 ``[0, 1, 0]``），
#: 目的是让"关节角度为正"对应"抬起来"，符合直觉。
#:
#: 图元（``parts`` 元素）：
#:   ``prim``     ``box`` / ``cylinder``
#:   ``at``       在段坐标系里的中心位置
#:   ``size``     长方体尺寸 [x, y, z]
#:   ``radius`` / ``height`` / ``axis``  圆柱参数（``axis`` 为 ``x``/``y``/``z``）
#:   ``color``    十六进制颜色
#:   ``jaw``      ``left`` / ``right``，用于夹爪开合动画
ARM_CHAIN: List[Dict] = [
    # --- 固定段：安装底板 + 地脚 ---
    {
        "joint": None, "label": "安装底板", "pivot": [0, 0, 0], "axis": None,
        "parts": [
            {"prim": "box", "at": [0, 0, 5], "size": [132, 118, 10],
             "color": COLORS["static"]},
            *_feet(53, 46, 7, 10, COLORS["foot"]),
        ],
    },
    # --- J1：底座回转（绕 Z）---
    {
        "joint": 1, "label": "底座回转", "pivot": [0, 0, 10], "axis": [0, 0, 1],
        "parts": [
            {"prim": "cylinder", "axis": "z", "at": [0, 0, 2],
             "radius": 34, "height": 4, "color": COLORS["foot"]},
            {"prim": "cylinder", "axis": "z", "at": [0, 0, 26],
             "radius": 27, "height": 52, "color": COLORS["column"]},
        ],
    },
    # --- J2：丝杆升降（绕 Y；蓝胶带模块 = 大臂）---
    {
        "joint": 2, "label": "丝杆升降", "pivot": [0, 0, 52], "axis": [0, -1, 0],
        "parts": [
            {"prim": "cylinder", "axis": "x", "at": [16, 0, 0],
             "radius": 27, "height": 32, "color": COLORS["disc"]},
            {"prim": "box", "at": [84, 0, 0], "size": [150, 54, 44],
             "color": COLORS["arm"]},
            {"prim": "box", "at": [40, 0, 0], "size": [34, 56, 46],
             "color": COLORS["tape"]},
            {"prim": "cylinder", "axis": "x", "at": [86, -18, 22],
             "radius": 5, "height": 140, "color": COLORS["screw"]},
            {"prim": "box", "at": [96, -18, 26], "size": [26, 22, 16],
             "color": COLORS["carriage"]},
        ],
    },
    # --- J3：肘部（大圆盘，绕 Y）---
    # 轴写 [0, 1, 0]：实测 reg0x01 增大时实物是往“下”转，
    # 所以模型里正角度必须对应往下，才和实物一致。
    {
        "joint": 3, "label": "肘部", "pivot": [168, 0, 0], "axis": [0, 1, 0],
        "parts": [
            {"prim": "cylinder", "axis": "y", "at": [0, 0, 0],
             "radius": 47, "height": 36, "color": COLORS["disc"]},
            {"prim": "cylinder", "axis": "y", "at": [0, 0, 0],
             "radius": 30, "height": 42, "color": COLORS["column"]},
            {"prim": "box", "at": [86, 0, 0], "size": [172, 22, 18],
             "color": COLORS["rod"]},
        ],
    },
    # --- J4：腕俯仰（末端圆盘，绕 Y）---
    {
        "joint": 4, "label": "腕俯仰", "pivot": [172, 0, 0], "axis": [0, -1, 0],
        "parts": [
            {"prim": "cylinder", "axis": "y", "at": [0, 0, 0],
             "radius": 41, "height": 32, "color": COLORS["disc"]},
            {"prim": "cylinder", "axis": "y", "at": [0, 0, 0],
             "radius": 26, "height": 38, "color": COLORS["column"]},
        ],
    },
    # --- J5：腕部（前臂长轴）---
    {
        "joint": 5, "label": "腕部", "pivot": [56, 0, 0], "axis": [1, 0, 0],
        "parts": [
            {"prim": "cylinder", "axis": "x", "at": [0, 0, 0],
             "radius": 24, "height": 50, "color": COLORS["column"]},
            {"prim": "box", "at": [50, 0, 0], "size": [100, 46, 38],
             "color": COLORS["wrist"]},
        ],
    },
    # --- J6：夹爪（直线开合）---
    {
        "joint": 6, "label": "夹爪", "pivot": [100, 0, 0], "axis": None,
        "parts": [
            {"prim": "box", "at": [16, 0, 0], "size": [32, 30, 22],
             "color": COLORS["tool"]},
            {"prim": "box", "at": [46, 0, 0], "size": [30, 12, 18],
             "color": COLORS["jaw"], "jaw": "left"},
            {"prim": "box", "at": [46, 0, 0], "size": [30, 12, 18],
             "color": COLORS["jaw"], "jaw": "right"},
        ],
    },
]

#: 夹爪两爪间距（mm）
JAW_OPEN_MM = 42.0
JAW_CLOSED_MM = 6.0


#: 关节控制语义
#:
#:   kind        yaw / pitch / roll / linear —— 决定面板展示与图例
#:   feedback    count（连续计数）/ zone（0·45·90 分区）
#:   range_deg   模型里的显示范围（伺服关节 = 实测行程；J1/J2/J6 为映射范围）
#:   zone_to_deg 分区值 -> 显示值 的映射
#:   control     servo / pulse / gripper
JOINTS: List[Dict] = [
    {
        "id": 1,
        "name": "J1",
        "label": "底座回转",
        "kind": "yaw",
        "feedback": "zone",
        "slave": 1,
        "range_deg": [-90.0, 90.0],
        "travel": "实测 2.5 s 脉冲约 90 度；分区 1 单位约 2 度",
        "zone_to_deg": {"0": -90.0, "45": 0.0, "90": 90.0},
        "control": "pulse",
        "directions": [["a", "正转 0xAA11"], ["b", "反转 0x0000"]],
        "pulse_default": 2.5,
        "pulse_max": 5.0,
        "hazard": "停止会撤掉力矩；无位置闭环，只能靠脉冲时长定位",
        "note": "分区值本身只表示 0/90/180 度三档，模型里的角度是估计值",
    },
    {
        "id": 2,
        "name": "J2",
        "label": "丝杆升降",
        "kind": "pitch",
        "feedback": "zone",
        "slave": 2,
        "range_deg": [0.0, 90.0],
        "travel": "上限位 = 垂直 90 度，下限位 = 接近水平 0 度",
        "zone_to_deg": {"0": 0.0, "45": 45.0, "90": 90.0},
        "control": "pulse",
        "directions": [["up", "上行（安全）"], ["down", "下行（<=2.5 s）"]],
        "pulse_default": 2.0,
        "pulse_max": 2.5,
        "hazard": "下行超过约 3.2 s 会触发硬刹车并把从站挂死，只能用短脉冲",
        "note": "中间位置无法精确控制，只能靠脉冲时长 + 目测",
    },
    {
        "id": 3,
        "name": "J3",
        "label": "肘部",
        "kind": "pitch",
        "feedback": "count",
        "slave": 3,
        "deg_per_count": 0.09,
        "range_deg": [0.0, 250.0],
        "home_deg": 180.0,
        "travel": "收拢姿态 = 0（L1/L2 完全折回，几何夹角 180°）；180° 时两杆共线",
        "control": "servo",
        "speed_max": 732,
        "speed_default": 250,
        "hazard": "停止 reg0x08=0 会失去保持力矩（J3 受重力，会自行落回收拢位）",
        "note": "1 count = 0.100897 度；**零点 = 上电时的收拢姿态**（单向行程）。"
                "**home_deg=180**：实物在传感器 0°（收拢）时 L1(J2→J3) 与 L2(J3→J4) "
                "是**完全折回**的（几何夹角 180°），而模型几何的 0° 才是共线 —— "
                "所以模型角 = 传感器角 + 180。几何夹角 = |180 − ((180+s) mod 360)|。"
                "角度变大时 L2 绕 J3 **顺时针**旋转（从正面看、J2 在左）。",
    },
    {
        "id": 4,
        "name": "J4",
        "label": "腕俯仰",
        "kind": "pitch",
        "feedback": "count",
        "slave": 4,
        "deg_per_count": 0.09,
        "range_deg": [0.0, 181.0],
        "home_deg": 180.0,
        "travel": "收拢姿态 = 0，单向正方向；180° 时细连杆与前臂体成一条直线",
        "control": "servo",
        "speed_max": 732,
        "speed_default": 250,
        "hazard": "停止会失去保持力矩",
        "note": "与 J3 同款固件；零点同样是上电时的收拢姿态。"
                "**home_deg=180**：J4 物理 180° 时细连杆与前臂体是一条直线，"
                "而模型几何的 0° 才是直线（所有连杆沿 +X），所以差 180°。"
                "若模型姿态仍对不上，在「模型」页调这个偏移"
    },
    {
        "id": 5,
        "name": "J5",
        "label": "腕部",
        "kind": "roll",
        "feedback": "count",
        "slave": 5,
        "deg_per_count": 0.237,
        "range_deg": [0.0, 161.0],
        "home_deg": 0.0,
        "travel": "681 count 约 161 度；pos=0 水平，377 = 90 度",
        "control": "servo",
        "speed_max": 732,
        "speed_default": 200,
        "hazard": "上电未使能，写目标前必须先设模式/速度/使能（SDK 已自动处理）",
        "note": "模型默认按'绕前臂长轴 roll'建模；若实物是这一轴在平面内摆动，"
                "请在『模型』面板把它的旋转轴改成 Y",
    },
    {
        "id": 6,
        "name": "J6",
        "label": "夹爪",
        "kind": "linear",
        "feedback": "zone",
        "slave": 6,
        "range_deg": [0.0, 100.0],
        "travel": "0 = 闭合，90 = 张开 180 度（约 42 mm）",
        "zone_to_deg": {"0": 0.0, "45": 50.0, "90": 100.0},
        "control": "gripper",
        "speed_max": 732,
        "speed_default": 100,
        "actions": [["open", "张开"], ["close", "闭合"]],
        "hazard": "没有夹取检测；停止会释放夹持力",
        "note": "寄存器只有 0/45/90 三档，模型里的开合是插值显示",
    },
]

def _soft_limits_deg(slave: int):
    """从 SDK 的软限位（原始计数）换算成本关节的可用角度区间。

    返回 ``[lo_deg, hi_deg]``（相对收拢姿态的 0°），无配置时返回 ``None``。
    """
    calib = SERVO_CALIBRATION.get(slave)
    if calib is None or not calib.limits:
        return None
    lo, hi = calib.limits
    return [round(calib.counts_to_deg(lo), 2), round(calib.counts_to_deg(hi), 2)]


#: 把 SDK 的换算率/零位/软限位附到伺服关节上
#: （UI 用它们做滑条范围、计数换算、预览可行性判断 —— 仍然是唯一数据源）
for _joint in JOINTS:
    _calib = SERVO_CALIBRATION.get(_joint["slave"])
    if _calib is None or _joint["control"] != "servo":
        continue
    _joint["deg_per_count"] = _calib.deg_per_count
    _joint["zero_counts"] = _calib.zero_counts
    _limits = _soft_limits_deg(_joint["slave"])
    if _limits is not None:
        _joint["soft_limits_deg"] = _limits


#: 连杆标注：在 3D 视图里标出 L1/L2/L3，便于现场对照。
#:
#: 约定（用户 2026-09-13 定义）：
#:   * **L1** = J2 与 J3 之间的臂（大臂）
#:   * **L2** = J3 与 J4 之间的臂（细连杆）
#:   * **L3** = J5 所在的臂（前臂体）
#:
#: ``seg_from`` / ``seg_to`` 是 ARM_CHAIN 的下标，标注画在两段轴心的中点。
LINKS: List[Dict] = [
    {"id": "L1", "label": "L1", "desc": "J2-J3 大臂", "seg_from": 2, "seg_to": 3},
    {"id": "L2", "label": "L2", "desc": "J3-J4 细连杆", "seg_from": 3, "seg_to": 4},
    {"id": "L3", "label": "L3", "desc": "J5 所在前臂", "seg_from": 5, "seg_to": 6},
]


#: 前端"帮助"面板显示的控制须知
CONTROL_NOTES = [
    "写命令前必须先做固件身份校验（页面上有『校验身份』按钮）。",
    "运动期间禁止读取寄存器 —— 本页面已按『发命令 → 静默等待 → 单次读』实现。",
    "伺服关节停止 = 关闭输出 = 失去保持力矩，停止前先支撑好机械臂。",
    "所有角度都是相对『上电零点』的；换个上电姿态要重新测量行程。",
    "禁止写 reg0x05（回零到 -2700 / 交替转速测试），SDK 已在传输层拦截。",
    "运动命令需要界面上先解锁（第二道闸门），且每次都会写进日志。",
]

#: 默认串口 / 波特率 / HTTP 端口
DEFAULT_SERIAL_PORT = "COM8"
DEFAULT_BAUD = 115200
DEFAULT_HTTP_PORT = 8765


def model_config() -> Dict:
    """返回给前端的完整模型配置。

    ⚠ 必须是**深拷贝**：调用方（模型覆盖合并、页面端编辑）会修改
    ``chain`` / ``joints`` 里的字段，直接返回 ``ARM_CHAIN`` / ``JOINTS`` 的引用
    会把出厂数据一起改掉 —— 那样「重置」就永远回不到出厂值了。
    """
    return {
        "units": UNITS,
        "colors": dict(COLORS),
        "chain": copy.deepcopy(ARM_CHAIN),
        "joints": copy.deepcopy(JOINTS),
        "links": copy.deepcopy(LINKS),
        "jaw": {"open_mm": JAW_OPEN_MM, "closed_mm": JAW_CLOSED_MM},
        "notes": list(CONTROL_NOTES),
        "defaults": {"serial_port": DEFAULT_SERIAL_PORT, "baud": DEFAULT_BAUD},
    }


def joint_by_id(joint_id: int) -> Dict:
    """按关节 id 取定义。"""
    for joint in JOINTS:
        if joint["id"] == joint_id:
            return joint
    raise KeyError("unknown joint id %r" % (joint_id,))


#: 从站地址 -> 关节定义
JOINT_BY_SLAVE: Dict[int, Dict] = {j["slave"]: j for j in JOINTS}


__all__ = [
    "UNITS", "COLORS", "ARM_CHAIN", "JAW_OPEN_MM", "JAW_CLOSED_MM",
    "JOINTS", "CONTROL_NOTES", "JOINT_BY_SLAVE", "LINKS",
    "DEFAULT_SERIAL_PORT", "DEFAULT_BAUD", "DEFAULT_HTTP_PORT",
    "model_config", "joint_by_id",
]
