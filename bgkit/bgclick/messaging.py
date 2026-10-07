# -*- coding: utf-8 -*-
"""bgkit.bgclick.messaging.py —— 消息投递：post / send 两条纯后台通道

从 bgclick.py 拆出的一节（源文件第 586-659, 2068-2084 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from .geometry import make_lparam
from .win32 import CWP_SKIPDISABLED, CWP_SKIPINVISIBLE, SMTO_ABORTIFHUNG, SMTO_BLOCK, WM_MOUSEMOVE, user32
from .wininfo import window_class



# --------------------------------------------------------------------------
# 发消息（关键：每一条都检查返回值）
# --------------------------------------------------------------------------


def post_checked(hwnd: int, msg: int, wparam: int, lparam: int) -> tuple[bool, int]:
    """PostMessageW 并检查结果。返回 (是否成功, 错误码)。"""
    ctypes.set_last_error(0)
    ok = user32.PostMessageW(hwnd, msg, wparam, lparam)
    err = ctypes.get_last_error()
    return bool(ok), (0 if ok else err)


def probe_window(hwnd: int) -> dict:
    """
    UIPI 探针：投递 WM_MOUSEMOVE，坐标故意指到窗口外 (9999, 9999)。

    为什么不用 WM_NULL？—— 实测踩坑：UIPI 只放行少数「无害」消息
    （WM_NULL、WM_MOVE 等），对 WM_NULL 一律放行，拿它当探针永远是假阳性。
    而鼠标类消息（WM_MOUSEMOVE / WM_LBUTTONDOWN / WM_USER / WM_APP ...）
    才会真正走 UIPI 过滤，和点击失败的原因**完全同源**。

    所以探针必须用鼠标消息。坐标指到 (9999,9999)（远在客户区外）+
    wParam=0（没有按键），窗口收到也只会忽略 —— 零副作用。

    返回 {"ok": 能否投递, "error": 错误码}
    """
    ctypes.set_last_error(0)
    ok = user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, make_lparam(9999, 9999))
    err = ctypes.get_last_error()
    return {"ok": bool(ok), "error": 0 if ok else err}


def probe_hierarchy(hwnd: int, x: int, y: int) -> dict:
    """
    从顶层窗口一路探到子控件，找出「最深的那个能接收消息的窗口」。

    这是实测踩出来的坑：顶层窗口探针通过，不代表子窗口也能通过 ——
    有些控件（尤其浏览器内核、跨进程托管的控件）本身就拒绝低权限消息。
    所以只探顶层是假阳性，必须逐层验。

    返回 {"chain": [...], "best": hwnd, "best_point": (x,y), "blocked": [...]}
    """
    chain = []
    cur, cx, cy = hwnd, int(x), int(y)
    best, best_point = hwnd, (int(x), int(y))
    blocked = []

    for _ in range(16):
        pr = probe_window(cur)
        chain.append({
            "hwnd": cur, "hwnd_hex": hex(cur), "class": window_class(cur),
            "point": [cx, cy], "ok": pr["ok"], "error": pr["error"],
        })
        if pr["ok"]:
            best, best_point = cur, (cx, cy)   # 能收就往下贪心，取最深的可收窗口
        else:
            blocked.append(chain[-1])
            break  # 这一层都进不去，更深的就不用试了

        child = user32.ChildWindowFromPointEx(
            cur, wintypes.POINT(cx, cy), CWP_SKIPINVISIBLE | CWP_SKIPDISABLED
        )
        if not child or child == cur:
            break
        pt = wintypes.POINT(cx, cy)
        if not (user32.ClientToScreen(cur, ctypes.byref(pt))
                and user32.ScreenToClient(int(child), ctypes.byref(pt))):
            break
        cur, cx, cy = int(child), pt.x, pt.y

    return {"chain": chain, "best": best, "best_point": best_point, "blocked": blocked}




def _dispatch_key_msg(hwnd: int, msg: int, wp: int, lp: int, method: str) -> tuple[bool, int]:
    """按 method 投递一条键盘消息。返回 (是否成功, 错误码)。"""
    if method == "send":
        out = ctypes.c_size_t(0)
        ctypes.set_last_error(0)
        res = user32.SendMessageTimeoutW(hwnd, msg, wp, lp,
                                         SMTO_ABORTIFHUNG | SMTO_BLOCK, 1000,
                                         ctypes.byref(out))
        err = ctypes.get_last_error()
        if res == 0 and err:
            return False, err
        # ★ SendMessageTimeout 返回 0 既可能是失败也可能是「处理结果就是 0」，
        #   所以只有 last_error 非 0 才算失败。
        return True, 0
    return post_checked(hwnd, msg, wp, lp)
