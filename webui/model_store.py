"""模型标定的持久化。

页面上的「模型」面板可以改：旋转轴、连杆长度（pivot）、零位偏移、夹爪开距、
伺服软限位。这些改动默认只存在浏览器内存里 —— 刷新就没了。

本模块把它们存成 :data:`OVERRIDE_PATH`（默认 ``webui/model_overrides.json``），
用 **覆盖文件** 的方式生效而不是回写 ``model_config.py``：

* 不用把 Python 源码当数据库写，不会因为写坏一个字符让服务起不来；
* 想恢复出厂值只要删掉这个文件（页面上的「重置」就是干这个）；
* 文件是普通 JSON，可以直接看 / 手改 / 进版本控制。

优先级：``model_overrides.json`` > ``model_config.py``
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from . import model_config

#: 覆盖文件路径（页面会把它显示出来）
OVERRIDE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "model_overrides.json")

#: 软限位的合理范围（计数）——防止手改 JSON 写出离谱的值
LIMIT_ABS_MAX = 20000


class ModelOverrideError(ValueError):
    """覆盖文件或上传的模型不合法。"""


# ----------------------------------------------------------------- 读写
def load_raw() -> Optional[Dict[str, Any]]:
    """读覆盖文件；不存在或损坏返回 None（损坏时会打印警告并忽略）。"""
    if not os.path.isfile(OVERRIDE_PATH):
        return None
    try:
        with open(OVERRIDE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        print("[model_store] 覆盖文件无法解析，已忽略: %s" % exc)
        return None
    if not isinstance(data, dict):
        print("[model_store] 覆盖文件格式不对（应为对象），已忽略")
        return None
    return data


def _write_raw(data: Dict[str, Any]) -> None:
    tmp = OVERRIDE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, OVERRIDE_PATH)


def clear() -> bool:
    """删除覆盖文件（回到 ``model_config.py`` 的出厂值）。"""
    if os.path.isfile(OVERRIDE_PATH):
        os.remove(OVERRIDE_PATH)
        return True
    return False


def info() -> Dict[str, Any]:
    """给页面用的覆盖文件信息。"""
    exists = os.path.isfile(OVERRIDE_PATH)
    out = {
        "path": OVERRIDE_PATH,
        "exists": exists,
        "saved_at": None,
    }
    if exists:
        raw = load_raw() or {}
        out["saved_at"] = raw.get("saved_at")
    return out


# ----------------------------------------------------------------- 校验
def _vec3(value: Any, name: str) -> List[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ModelOverrideError("%s 必须是 3 个数字" % name)
    out = []
    for v in value:
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ModelOverrideError("%s 含有非数字" % name)
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            raise ModelOverrideError("%s 含有非法数值" % name)
        if abs(f) > 5000:
            raise ModelOverrideError("%s 数值过大（>5000）" % name)
        out.append(f)
    return out


def _axis(value: Any, name: str) -> Optional[List[int]]:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ModelOverrideError("%s 必须是 3 个 -1/0/1" % name)
    for v in value:
        if v not in (-1, 0, 1):
            raise ModelOverrideError("%s 只能取 -1 / 0 / 1" % name)
    if all(v == 0 for v in value):
        raise ModelOverrideError("%s 不能是零向量" % name)
    return [int(v) for v in value]


def extract(model: Dict[str, Any]) -> Dict[str, Any]:
    """从页面传来的**完整模型**里抽出可持久化的字段并校验。

    :raises ModelOverrideError: 数据不合法（页面会显示具体原因）
    """
    base = model_config.model_config()
    chain = model.get("chain")
    if not isinstance(chain, list) or len(chain) != len(base["chain"]):
        raise ModelOverrideError(
            "chain 段数不符：期望 %d，收到 %s"
            % (len(base["chain"]), len(chain) if isinstance(chain, list) else type(chain).__name__))

    segments = []
    for i, (seg, ref) in enumerate(zip(chain, base["chain"])):
        if not isinstance(seg, dict):
            raise ModelOverrideError("chain[%d] 不是对象" % i)
        if seg.get("joint") != ref["joint"]:
            raise ModelOverrideError(
                "chain[%d] 的 joint 不符（期望 %s，收到 %s）—— 模型结构被改过了？"
                % (i, ref["joint"], seg.get("joint")))
        segments.append({
            "joint": ref["joint"],
            "pivot": _vec3(seg.get("pivot", ref["pivot"]), "chain[%d].pivot" % i),
            "axis": _axis(seg.get("axis", ref["axis"]), "chain[%d].axis" % i),
        })

    home: Dict[str, float] = {}
    jaw = model.get("jaw") or {}
    jaw_out = {
        "open_mm": float(base["jaw"]["open_mm"]),
        "closed_mm": float(base["jaw"]["closed_mm"]),
    }
    for key in ("open_mm", "closed_mm"):
        if key in jaw:
            v = jaw[key]
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not (0 < float(v) < 500):
                raise ModelOverrideError("jaw.%s 必须是 0~500 的数字" % key)
            jaw_out[key] = float(v)
    if jaw_out["closed_mm"] >= jaw_out["open_mm"]:
        raise ModelOverrideError("夹爪：全闭间距必须小于全开间距")

    limits: Dict[str, List[int]] = {}
    for j in model.get("joints") or []:
        if not isinstance(j, dict):
            continue
        jid = j.get("id")
        ref = next((x for x in base["joints"] if x["id"] == jid), None)
        if ref is None:
            continue
        if "home_deg" in j:
            v = j["home_deg"]
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise ModelOverrideError("J%s.home_deg 必须是数字" % jid)
            if abs(float(v)) > 720:
                raise ModelOverrideError("J%s.home_deg 超出 ±720°" % jid)
            home[str(jid)] = float(v)
        if ref["control"] == "servo" and "soft_limits_deg" in j:
            sl = j["soft_limits_deg"]
            if not isinstance(sl, (list, tuple)) or len(sl) != 2:
                raise ModelOverrideError("J%s.soft_limits_deg 必须是 [lo, hi]" % jid)
            lo, hi = float(sl[0]), float(sl[1])
            if not lo < hi:
                raise ModelOverrideError("J%s 软限位必须 lo < hi" % jid)
            dpc = ref.get("deg_per_count") or 0.1
            cnt = [int(round(lo / dpc)), int(round(hi / dpc))]
            if max(abs(cnt[0]), abs(cnt[1])) > LIMIT_ABS_MAX:
                raise ModelOverrideError(
                    "J%s 软限位换算成计数后超出 ±%d，拒绝保存" % (jid, LIMIT_ABS_MAX))
            limits[str(jid)] = cnt

    return {
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "Web 控制台「保存为默认」写入；优先级高于 model_config.py。"
                "删掉本文件或点页面「重置」即可回到出厂值。",
        "segments": segments,
        "home_deg": home,
        "jaw": jaw_out,
        "soft_limits_counts": limits,
    }


def save(model: Dict[str, Any]) -> Dict[str, Any]:
    """校验并落盘。返回写入的内容。"""
    data = extract(model)
    _write_raw(data)
    return data


# ----------------------------------------------------------------- 合并
def merge_into(model: Dict[str, Any], raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把覆盖值合并进基础模型（原地修改并返回 ``model``）。"""
    raw = load_raw() if raw is None else raw
    if not raw:
        return model

    segs = raw.get("segments") or []
    if len(segs) == len(model["chain"]):
        for seg, override in zip(model["chain"], segs):
            if override.get("joint") != seg.get("joint"):
                continue        # 结构变了，忽略这条
            if override.get("pivot"):
                seg["pivot"] = list(override["pivot"])
            if "axis" in override:
                seg["axis"] = list(override["axis"]) if override["axis"] else None

    home = raw.get("home_deg") or {}
    for j in model["joints"]:
        key = str(j["id"])
        if key in home:
            j["home_deg"] = float(home[key])

    if raw.get("jaw"):
        model["jaw"] = dict(raw["jaw"])

    limits = raw.get("soft_limits_counts") or {}
    for j in model["joints"]:
        key = str(j["id"])
        if j.get("control") != "servo" or key not in limits:
            continue
        lo, hi = limits[key]
        dpc = j.get("deg_per_count") or 0.1
        j["soft_limits_deg"] = [round(lo * dpc, 3), round(hi * dpc, 3)]
        j["soft_limits_counts"] = [int(lo), int(hi)]

    model["overrides_applied"] = True
    return model


def merged_model() -> Dict[str, Any]:
    """页面 / ``GET /api/model`` 用的最终模型。"""
    model = model_config.model_config()
    merge_into(model)
    model["saved"] = info()
    return model


def configured_limits() -> Dict[int, tuple]:
    """返回覆盖文件里配了的软限位 ``{从站: (lo, hi)}``（计数）。"""
    raw = load_raw() or {}
    limits = raw.get("soft_limits_counts") or {}
    by_slave = {j["id"]: j["slave"] for j in model_config.JOINTS}
    out: Dict[int, tuple] = {}
    for key, pair in limits.items():
        try:
            jid = int(key)
        except ValueError:
            continue
        slave = by_slave.get(jid)
        if slave is None or not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        out[slave] = (int(pair[0]), int(pair[1]))
    return out


def apply_soft_limits(arm) -> Dict[str, Any]:
    """把保存的软限位推给一个已连接的 :class:`RobotArm`。

    软限位是安全边界，所以这里只做“把已保存的值装回去”，
    改值本身仍然要走页面上带确认的接口。
    """
    applied = {}
    for slave, (lo, hi) in configured_limits().items():
        try:
            joint = arm[slave]
            joint.set_limits(lo, hi)
            applied[str(slave)] = [lo, hi]
        except Exception as exc:  # noqa: BLE001
            print("[model_store] 应用从站 %d 的软限位失败: %s" % (slave, exc))
    return applied


__all__ = [
    "OVERRIDE_PATH", "ModelOverrideError",
    "load_raw", "clear", "info", "save", "extract",
    "merge_into", "merged_model", "configured_limits", "apply_soft_limits",
]
