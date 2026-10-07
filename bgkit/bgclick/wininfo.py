# -*- coding: utf-8 -*-
"""bgkit.bgclick.wininfo.py —— 窗口查询：标题 / 类名 / 进程 / 完整性级别 / 枚举

从 bgclick.py 拆出的一节（源文件第 359-485 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from typing import Iterator, Optional
from .win32 import EnumWindowsProc, PROCESS_QUERY_LIMITED_INFORMATION, TOKEN_QUERY, TokenElevation, advapi32, kernel32, shell32, user32



# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------


def enable_dpi_awareness() -> Optional[str]:
    """开启 DPI 感知，保证 GetClientRect / ClientToScreen 在缩放显示器上坐标不错位。"""
    try:
        # -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except AttributeError:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return "per-monitor"
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
        return "system"
    except Exception:
        return None


def is_admin() -> bool:
    """当前进程是否已提权。"""
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


def window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def process_name(pid: int) -> str:
    """拿进程可执行文件名。拿不到就返回空串（比如权限不足）。"""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1]
        return ""
    finally:
        kernel32.CloseHandle(h)


def process_is_elevated(pid: int) -> Optional[bool]:
    """读目标进程令牌的 TokenElevation。拿不到返回 None（不代表没提权！）。"""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            # TOKEN_ELEVATION 就是一个 DWORD
            val = wintypes.DWORD(0)
            ret_len = wintypes.DWORD(0)
            ok = advapi32.GetTokenInformation(
                token, TokenElevation, ctypes.byref(val),
                ctypes.sizeof(val), ctypes.byref(ret_len)
            )
            if not ok:
                return None
            return bool(val.value)
        finally:
            kernel32.CloseHandle(token)
    finally:
        kernel32.CloseHandle(h)


def iter_top_level_windows() -> Iterator[int]:
    """遍历所有顶层窗口句柄。"""
    handles: list[int] = []

    def _cb(hwnd, _lparam):
        handles.append(int(hwnd))
        return True

    user32.EnumWindows(EnumWindowsProc(_cb), 0)
    return iter(handles)


def describe(hwnd: int, with_process: bool = True) -> dict:
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    client = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(client))
    info = {
        "hwnd": hwnd,
        "hwnd_hex": hex(hwnd),
        "title": window_title(hwnd),
        "class": window_class(hwnd),
        "pid": window_pid(hwnd),
        "visible": bool(user32.IsWindowVisible(hwnd)),
        "rect": [rect.left, rect.top, rect.right, rect.bottom],
        "client_size": [client.right - client.left, client.bottom - client.top],
    }
    if with_process:
        info["process"] = process_name(info["pid"])
    return info
