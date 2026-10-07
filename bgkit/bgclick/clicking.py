# -*- coding: utf-8 -*-
"""bgkit.bgclick.clicking.py —— 点击：post / send / hardware / sendinput 四条通道

从 bgclick.py 拆出的一节（源文件第 660-678, 747-831, 944-988 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import time
from .errors import AppError
from .geometry import make_lparam, resolve_screen_point
from .messaging import post_checked, probe_hierarchy
from .sendinput import MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_MOVE, MOUSEEVENTF_VIRTUALDESK, _mk_mouse_input, _send_inputs, _to_absolute
from .win32 import BUTTONS, ERROR_ACCESS_DENIED, HW_BUTTONS, SMTO_ABORTIFHUNG, SMTO_BLOCK, WM_MOUSEMOVE, user32



def resolving_click(hwnd: int, x: int, y: int, args) -> dict:
    """
    智能投递：先探层级，挑最深的可投递窗口去点。
    这样既命中正确的控件，又不会因为控制权不够而白跑一趟。
    """
    info = probe_hierarchy(hwnd, x, y)
    if info["blocked"]:
        # 有层被拦：退到最后一个能通的窗口，并把父窗口当作备选
        parent = user32.GetParent(info["blocked"][0]["hwnd"])
        rec = do_click(info["best"], *info["best_point"], args)
        rec["fallback_from"] = info["blocked"][0]["hwnd_hex"]
        rec["blocked_at"] = info["blocked"][0]
        rec["parent_available"] = bool(parent)
        return rec
    rec = do_click(info["best"], *info["best_point"], args)
    rec["hierarchy"] = [c["hwnd_hex"] for c in info["chain"]]
    return rec




def send_click_post(hwnd: int, x: int, y: int, button: str = "left",
                    move_first: bool = True) -> dict:
    """PostMessage 投递一次点击。返回结果字典，失败时报出真错误码。"""
    down, up, mk = BUTTONS[button]
    lp = make_lparam(x, y)
    result = {"method": "post", "hwnd": hwnd, "point": [x, y], "messages": [], "ok": True,
              "error": 0}

    steps = []
    if move_first:
        # 补一个移动消息：不少程序靠它更新内部 hover 状态，直接点会被忽略
        steps.append((WM_MOUSEMOVE, 0))
    steps.append((down, mk))
    steps.append((up, 0))

    for i, (msg, wp) in enumerate(steps):
        ok, err = post_checked(hwnd, msg, wp, lp)
        result["messages"].append({"msg": hex(msg), "ok": ok, "error": err})
        if not ok:
            result["ok"] = False
            result["error"] = err
            return result
        if i < len(steps) - 1:
            time.sleep(0.01 if msg == WM_MOUSEMOVE else 0.03)
    return result


def send_click_send(hwnd: int, x: int, y: int, button: str = "left") -> dict:
    """
    SendMessageTimeout 同步投递。会等目标处理完，某些忽略 Post 的程序吃这套。
    仍然不动光标；但目标卡死时会阻塞到超时。
    """
    down, up, mk = BUTTONS[button]
    lp = make_lparam(x, y)
    result = {"method": "send", "hwnd": hwnd, "point": [x, y], "messages": [], "ok": True,
              "error": 0}
    out = ctypes.c_size_t(0)
    for msg, wp in ((WM_MOUSEMOVE, 0), (down, mk), (up, 0)):
        ctypes.set_last_error(0)
        res = user32.SendMessageTimeoutW(hwnd, msg, wp, lp,
                                         SMTO_ABORTIFHUNG | SMTO_BLOCK, 1000, ctypes.byref(out))
        err = ctypes.get_last_error()
        if res == 0:
            result["messages"].append({"msg": hex(msg), "ok": False, "error": err})
            result["ok"] = False
            result["error"] = err or 1
            return result
        result["messages"].append({"msg": hex(msg), "ok": True, "error": 0})
    return result


def send_click_hardware(hwnd: int, x: int, y: int, button: str = "left",
                        restore_cursor: bool = False) -> dict:
    """
    ★ 真点击：SetCursorPos + mouse_event。
    会真的移动你的光标（前台会被打扰几毫秒），但**所有程序都吃**。
    只在 post/send 都不灵时用。restore_cursor=True 会在点完后把光标放回原处。
    """
    down, up = HW_BUTTONS[button]
    sx, sy = resolve_screen_point(hwnd, x, y)
    saved = wintypes.POINT()
    had_saved = bool(restore_cursor and user32.GetCursorPos(ctypes.byref(saved)))

    ok = user32.SetCursorPos(sx, sy)
    if not ok:
        # 坐标在虚拟屏幕外（多显示器不连续排列时可能发生）
        return {"method": "hardware", "hwnd": hwnd, "point": [x, y],
                "screen_point": [sx, sy], "messages": [], "ok": False,
                "error": ctypes.get_last_error() or ERROR_ACCESS_DENIED,
                "detail": f"SetCursorPos 到 ({sx},{sy}) 失败，取消真点击以免误点"}

    time.sleep(0.02)
    user32.mouse_event(down, 0, 0, 0, None)
    time.sleep(0.03)
    user32.mouse_event(up, 0, 0, 0, None)

    if had_saved:
        time.sleep(0.02)
        user32.SetCursorPos(saved.x, saved.y)

    return {"method": "hardware", "hwnd": hwnd, "point": [x, y],
            "screen_point": [sx, sy], "messages": [], "ok": True, "error": 0,
            "cursor_restored": had_saved}




def do_click(hwnd: int, x: int, y: int, args) -> dict:
    if args.method == "post":
        return send_click_post(hwnd, x, y, args.button)
    if args.method == "send":
        return send_click_send(hwnd, x, y, args.button)
    if args.method == "sendinput":
        return send_input_click(hwnd, x, y, args.button,
                                restore_cursor=getattr(args, "restore_cursor", True))
    return send_click_hardware(hwnd, x, y, args.button, restore_cursor=args.restore_cursor)


def send_input_click(hwnd: int, x: int, y: int, button: str = "left",
                     restore_cursor: bool = True) -> dict:
    """
    SendInput 版点击：移动 + 按下 + 抬起**一次整批提交**。

    ★ 比 mouse_event 逐条发更原子：中途不会被用户真实鼠标动作插进来，
      也不会出现「按下之后、抬起之前冒出来一个别的点击」。
    """
    if button not in HW_BUTTONS:
        raise AppError(f"button 只能是 {list(HW_BUTTONS)}，收到 {button!r}", code=2)
    down, up = HW_BUTTONS[button]
    sx, sy = resolve_screen_point(hwnd, x, y)
    saved = wintypes.POINT()
    had_saved = bool(user32.GetCursorPos(ctypes.byref(saved)))

    ax, ay = _to_absolute(sx, sy)
    inputs = [
        _mk_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay),
        _mk_mouse_input(down),
        _mk_mouse_input(up),
    ]
    sent, err = _send_inputs(inputs)

    restored = False
    if restore_cursor and had_saved:
        time.sleep(0.02)
        restored = bool(user32.SetCursorPos(saved.x, saved.y))

    return {"method": "sendinput", "hwnd": hwnd, "point": [x, y],
            "screen_point": [sx, sy], "messages": [], "ok": sent == len(inputs),
            "sent": sent, "expected": len(inputs), "error": err,
            "cursor_restored": restored}
