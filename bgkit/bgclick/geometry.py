# -*- coding: utf-8 -*-
"""bgkit.bgclick.geometry.py —— 坐标换算与窗口几何

从 bgclick.py 拆出的一节（源文件第 525-585, 2480-2488 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from .win32 import CWP_SKIPDISABLED, CWP_SKIPINVISIBLE, user32
from .wininfo import window_class



# --------------------------------------------------------------------------
# 坐标换算
# --------------------------------------------------------------------------


def resolve_client_point(hwnd: int, x: int, y: int) -> tuple[int, int]:
    """屏幕坐标 -> 客户区坐标。"""
    pt = wintypes.POINT(int(x), int(y))
    if not user32.ScreenToClient(hwnd, ctypes.byref(pt)):
        raise OSError(f"ScreenToClient 失败：错误码 {ctypes.get_last_error()}")
    return pt.x, pt.y


def resolve_screen_point(hwnd: int, x: int, y: int) -> tuple[int, int]:
    """客户区坐标 -> 屏幕坐标。"""
    pt = wintypes.POINT(int(x), int(y))
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        raise OSError(f"ClientToScreen 失败：错误码 {ctypes.get_last_error()}")
    return pt.x, pt.y


def make_lparam(x: int, y: int) -> int:
    """
    把客户区坐标打包成鼠标消息的 LPARAM。

    低 16 位 = x，高 16 位 = y，各自按**有符号 16 位**解释。
    这里用 & 0xFFFF 把任意整数截断成 16 位：负数会变成补码形式
    （-1 -> 0xFFFF），恰好符合 Win32 的约定。
    注意：调用方应保证 x/y 落在客户区内（非负），否则坐标会被解释成负数。
    """
    return (int(y) & 0xFFFF) << 16 | (int(x) & 0xFFFF)


def deepest_child_at(hwnd: int, x: int, y: int) -> tuple[int, int, int, list[str]]:
    """
    从 hwnd 的客户区坐标 (x, y) 向下钻到最深的可见子窗口。

    为什么要钻：标准控件（按钮、输入框、列表项）都是**子窗口**，
    父窗口收到鼠标消息后并不会转发给它。发给顶层窗口 = 石沉大海。

    返回 (最终窗口句柄, 该窗口内的 x, y, 途经的类名列表)
    """
    cur, cx, cy = hwnd, int(x), int(y)
    path = [window_class(hwnd)]
    for _ in range(16):  # 防止病态嵌套
        child = user32.ChildWindowFromPointEx(
            cur, wintypes.POINT(cx, cy), CWP_SKIPINVISIBLE | CWP_SKIPDISABLED
        )
        if not child or child == cur:
            break
        # (cx, cy) 先换算成屏幕坐标，再换算进子窗口的客户区
        pt = wintypes.POINT(cx, cy)
        if not user32.ClientToScreen(cur, ctypes.byref(pt)):
            break
        if not user32.ScreenToClient(child, ctypes.byref(pt)):
            break
        cur, cx, cy = int(child), pt.x, pt.y
        path.append(window_class(cur))
    return cur, cx, cy, path




def window_dimensions(hwnd: int, client_only: bool = False) -> tuple[int, int]:
    r = wintypes.RECT()
    if client_only:
        user32.GetClientRect(hwnd, ctypes.byref(r))
        return r.right - r.left, r.bottom - r.top
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top
