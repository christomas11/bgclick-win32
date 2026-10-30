# -*- coding: utf-8 -*-
"""bgkit.bgclick.refstore.py —— UIA 元素的短引用（@e1 / @e2 …）

## 为什么要这个

UIA 能算出元素的屏幕坐标，但把坐标写死在脚本里非常脆：窗口挪一下、分辨率改一下，
点击就飞到别处去了。给它编个号，调用方只说「点 @e3」，由程序去查它现在在哪儿。

## 会怎么失效，以及怎么处理

ref 是「上次扫描那一刻的快照」。树变了（换了页面、列表刷新）就会过期。所以：

  * 每条 ref 都带 hwnd 和扫描时间
  * 用之前先校验窗口还在（IsWindow）
  * 元素重扫不到时**明确报错**，不静默点一个旧坐标

点击路径上如果第一次没打中，调用方可以走 refresh() 重扫一次再试 —— 见 cli.cmd_click。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

from .errors import AppError
from .win32 import user32

REF_PREFIX = "@e"
_MAX_AGE = 3600          # 秒；超过这么久就当过期，别拿一小时前的坐标去点


class RefError(AppError):
    """ref 不存在 / 过期 / 对应窗口已关闭。"""


def _state_file() -> str:
    # 运行期导入，避免 bgclick 包与 bgclient 包互相 import 成环
    from bgkit.bgclient.paths import state_dir
    return os.path.join(state_dir(), "refs.json")


def normalize(ref: str) -> str:
    """接受 @e3 / e3 / 3 三种写法，统一成 @e3。"""
    s = str(ref).strip()
    if s.startswith(REF_PREFIX):
        return s
    if s.startswith("e") and s[1:].isdigit():
        return REF_PREFIX + s[1:]
    if s.isdigit():
        return REF_PREFIX + s
    raise RefError(f"看不懂的 ref：{ref!r}（应该是 @e1 这种）")


def iter_refs(elements: list):
    """按固定规则产出 (ref 或 None, 元素)。describe_tree 与 assign 共用同一套规则，
    否则「树上看到的编号」和「保存下来的编号」会对不上 —— 那就点错东西了。"""
    n = 0
    for el in elements:
        if getattr(el, "rect", None):
            n += 1
            yield f"{REF_PREFIX}{n}", el
        else:
            yield None, el


def assign(elements: list, hwnd: int) -> dict[str, dict]:
    """
    给一批元素编号。只对**有矩形**的元素编号 —— 没有坐标的元素点了也没用。
    编号顺序就是遍历顺序，所以同一个界面重复扫描一般会得到同样的编号。
    """
    mapping: dict[str, dict] = {}
    for ref, el in iter_refs(elements):
        if ref is None:
            continue
        d = el.to_dict(hwnd)
        mapping[ref] = {
            "name": d.get("name", ""),
            "control_type": d.get("control_type", ""),
            "automation_id": d.get("automation_id", ""),
            "class_name": d.get("class_name", ""),
            "rect": el.rect,
            "center": d.get("center"),
            "client_center": d.get("client_center"),
            "is_interactive": d.get("is_interactive"),
        }
    return mapping


def save(hwnd: int, mapping: dict[str, dict]) -> None:
    """把这次扫描的结果落盘。失败不影响主流程（缓存丢了最多重扫）。"""
    payload = {
        "hwnd": int(hwnd),
        "hwnd_hex": hex(hwnd),
        "saved_at": time.time(),
        "saved_at_text": time.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(mapping),
        "refs": mapping,
    }
    path = _state_file()
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        pass


def load() -> dict[str, Any]:
    path = _state_file()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def resolve(ref: str, allow_stale: bool = False) -> dict:
    """
    把一个 ref 解成「现在该点哪儿」。

    返回 {hwnd, hwnd_hex, client_center, screen_center, name, control_type, age}
    校验不过就抛 RefError，并给一句能直接照做的提示。
    """
    key = normalize(ref)
    data = load()
    if not data:
        raise RefError(
            "还没有任何扫描记录。先跑一次 uia 拿编号：\n"
            "  python bgclick.py --title \"记事本\" --uia"
        )

    refs = data.get("refs") or {}
    rec = refs.get(key)
    if not rec:
        more = "，".join(sorted(refs)[:8]) or "（无）"
        raise RefError(
            f"{key} 不在上次扫描结果里。上次扫的是 {data.get('hwnd_hex')}"
            f"（{data.get('saved_at_text')}），共 {len(refs)} 个元素，例如：{more}\n"
            f"重新扫描：python bgclick.py --hwnd {data.get('hwnd_hex')} --uia"
        )

    hwnd = int(data.get("hwnd") or 0)
    if not hwnd or not user32.IsWindow(hwnd):
        raise RefError(
            f"{key} 对应的窗口（{data.get('hwnd_hex')}）已经不存在了 —— 窗口被关掉了？\n"
            f"重新找窗口并扫描：python bgclick.py --title \"...\" --uia"
        )

    age = time.time() - float(data.get("saved_at") or 0)
    if age > _MAX_AGE and not allow_stale:
        raise RefError(
            f"{key} 的扫描快照已经过去 {int(age / 60)} 分钟，太旧了（上限 {_MAX_AGE // 60} 分钟）。\n"
            f"确认窗口没变可以加 --allow-stale 强行使用，否则先重扫。"
        )

    center = rec.get("client_center")
    if not center:
        # 存的时候没能换算成客户区坐标（窗口当时可能最小化），这里再试一次
        sc = rec.get("center") or []
        if len(sc) == 2:
            from .geometry import resolve_client_point
            try:
                center = list(resolve_client_point(hwnd, sc[0], sc[1]))
            except OSError:
                center = None
    if not center:
        raise RefError(f"{key}（{rec.get('control_type')} {rec.get('name')!r}）没有可用坐标")

    return {
        "ref": key,
        "hwnd": hwnd,
        "hwnd_hex": data.get("hwnd_hex"),
        "name": rec.get("name", ""),
        "control_type": rec.get("control_type", ""),
        "screen_center": rec.get("center"),
        "client_center": list(center),
        "age": age,
    }


def refresh(ref: str) -> Optional[dict]:
    """
    重扫对应窗口，按「控件类型 + 名字 + AutomationId」找回同一个元素。

    找到了返回新的 resolve 结果（坐标是新的），找不到返回 None，
    由调用方决定是报错还是退回旧坐标。
    """
    key = normalize(ref)
    data = load()
    hwnd = int(data.get("hwnd") or 0)
    rec = (data.get("refs") or {}).get(key)
    if not hwnd or not rec or not user32.IsWindow(hwnd):
        return None

    from . import uia as uiamod
    ok, _why = uiamod.uia_available()
    if not ok:
        return None

    want_name = rec.get("name") or ""
    want_type = rec.get("control_type") or ""
    want_aid = rec.get("automation_id") or ""

    res = uiamod.find(hwnd, name=want_name or None,
                      control_type=want_type or None,
                      automation_id=want_aid or None,
                      exact=True, limit=200)
    if not res.elements:
        return None

    # 多个同名元素时，挑离旧位置最近的那个 —— 界面小幅重排也不会点错行
    old = rec.get("center") or [0, 0]
    best = min(res.elements, key=lambda e: (
        (e.screen_center[0] - old[0]) ** 2 + (e.screen_center[1] - old[1]) ** 2
        if e.screen_center else 1 << 62
    ))
    if not best.rect:
        return None

    mapping = (data.get("refs") or {}).copy()
    mapping[key] = {
        "name": best.name,
        "control_type": best.control_type,
        "automation_id": best.automation_id,
        "class_name": best.class_name,
        "rect": best.rect,
        "center": best.screen_center,
        "client_center": best.center_in_client(hwnd),
        "is_interactive": best.is_interactive,
    }
    save(hwnd, mapping)
    return resolve(key, allow_stale=True)
