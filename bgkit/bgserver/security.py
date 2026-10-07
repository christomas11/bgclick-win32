# -*- coding: utf-8 -*-
"""bgkit.bgserver.security.py —— 参数校验与安全助手

从 窗口工作.py 拆出的一节（源文件第 208-366 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

from bgkit import bgclick as bc
import argparse
import os
import time
from typing import Any, Optional
from .config import ALLOWED_SHOT_ROOTS



# --------------------------------------------------------------------------
# 参数校验与安全助手
# --------------------------------------------------------------------------


def check_host_header(host: Optional[str]) -> bool:
    """
    只接受本机 Host。防 DNS rebinding：
    恶意网页可以把自己解析到 127.0.0.1 来访问本地服务，
    但它发来的 Host 头会是自己的域名，被这里挡住。
    """
    if not host:
        return False
    h = host.strip().lower()
    if h.startswith("["):                     # IPv6 形如 [::1]:8765
        h = h[1:].split("]")[0]
    else:
        h = h.split(":")[0]
    return h in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def resolve_shot_path(user_path: Optional[str], default_name: str) -> str:
    """
    把用户给的截图路径解析成绝对路径，并确保落在允许的根目录内。
    越界直接拒绝 —— 防止这个提权服务被用来覆盖任意文件。
    """
    if not user_path:
        target = os.path.join(ALLOWED_SHOT_ROOTS[0], default_name)
    else:
        target = user_path
        if not os.path.isabs(target):
            target = os.path.join(ALLOWED_SHOT_ROOTS[0], target)
    target = os.path.abspath(target)

    for root in ALLOWED_SHOT_ROOTS:
        root_abs = os.path.abspath(root)
        try:
            if os.path.commonpath([target, root_abs]) == root_abs:
                return target
        except ValueError:
            continue  # 不同盘符，肯定不在这个根下面
    raise bc.AppError(
        f"截图路径越界：{target}\n只允许写到这些目录下：\n  "
        + "\n  ".join(ALLOWED_SHOT_ROOTS), code=2)


def clamp_int(value: Any, lo: int, hi: int, name: str, default: int) -> int:
    if value is None:
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise bc.AppError(f"{name} 必须是整数，收到 {value!r}", code=2)
    if n < lo or n > hi:
        raise bc.AppError(f"{name} 超出允许范围 [{lo}, {hi}]，收到 {n}", code=2)
    return n


def find_target(body: dict) -> dict:
    """从请求体里定位目标窗口，返回 describe() 结果。"""
    args = argparse.Namespace(
        hwnd=body.get("hwnd"),
        title=body.get("title"),
        regex=bool(body.get("regex")),
        exact=bool(body.get("exact")),
        process=body.get("process"),
        only_visible=not bool(body.get("include_hidden")),
        index=int(body.get("index", 0)),
    )
    if isinstance(args.hwnd, str):
        args.hwnd = int(args.hwnd, 0)
    cands = bc.match_windows(args)
    if not cands:
        raise bc.AppError("没找到匹配的窗口", code=1)
    if args.index >= len(cands) or args.index < 0:
        raise bc.AppError(f"--index {args.index} 越界（匹配到 {len(cands)} 个）", code=2)
    return cands[args.index]


def click_target(target: dict, body: dict, max_clicks: int) -> dict:
    """对已解析的目标窗口执行点击。"""
    hwnd = target["hwnd"]
    cw, ch = target["client_size"]

    count = clamp_int(body.get("count"), 0, max_clicks, "count", 1)
    interval = float(body.get("interval", 0.5))
    if not (0 <= interval <= 3600):
        raise bc.AppError("interval 必须在 0~3600 秒之间", code=2)
    jitter = clamp_int(body.get("jitter"), 0, 500, "jitter", 0)

    button = body.get("button", "left")
    if button not in bc.BUTTONS:
        raise bc.AppError(f"button 只能是 {list(bc.BUTTONS)}，收到 {button!r}", code=2)
    method = body.get("method", "post")
    if method not in ("post", "send", "hardware"):
        raise bc.AppError("method 只能是 post / send / hardware", code=2)

    # 坐标
    if body.get("center"):
        base = (cw // 2, ch // 2)
    elif body.get("pos"):
        base = (int(body["pos"][0]), int(body["pos"][1]))
    elif body.get("screen_pos"):
        base = bc.resolve_client_point(hwnd, int(body["screen_pos"][0]),
                                       int(body["screen_pos"][1]))
    else:
        raise bc.AppError("必须给 center / pos / screen_pos 之一", code=2)

    if not (0 <= base[0] < cw and 0 <= base[1] < ch):
        raise bc.AppError(f"坐标 {base} 落在客户区 {cw}x{ch} 之外", code=2)

    args = argparse.Namespace(
        button=button, method=method, deep=not bool(body.get("no_deep")),
        restore_cursor=bool(body.get("restore_cursor", True)),
    )

    if body.get("activate") and method != "hardware":
        bc.user32.SetForegroundWindow(hwnd)
        time.sleep(0.15)

    results = []
    done = 0
    failed = 0
    while count == 0 or done < count:
        if not bc.user32.IsWindow(hwnd):
            break
        x, y = base
        if jitter:
            import random
            x += random.randint(-jitter, jitter)
            y += random.randint(-jitter, jitter)
        x, y = max(0, min(x, cw - 1)), max(0, min(y, ch - 1))

        if args.deep and method != "hardware":
            rec = bc.resolving_click(hwnd, x, y, args)
        else:
            rec = bc.do_click(hwnd, x, y, args)
        rec["index"] = done
        results.append(rec)
        if not rec["ok"]:
            failed += 1
            if not body.get("force_continue"):
                break
        done += 1
        if count != 0 and done >= count:
            break
        if count == 0 and done >= max_clicks:   # 无限循环也要有个天花板
            break
        time.sleep(max(0.0, interval))

    return {
        "hwnd": target["hwnd_hex"], "title": target["title"],
        "process": target["process"], "base_pos": list(base),
        "clicked": done, "failed": failed,
        "ok": failed == 0 and done > 0,
        "results": results[-10:],   # 只回最近 10 条，别把响应撑爆
    }
