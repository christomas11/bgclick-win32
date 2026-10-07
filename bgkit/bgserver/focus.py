# -*- coding: utf-8 -*-
"""bgkit.bgserver.focus.py —— 跨进程拿目标窗口的焦点控件

从最初的单文件脚本拆出的一节。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

from bgkit import bgclick as bc
import ctypes
from typing import Optional



# --------------------------------------------------------------------------
# 跨进程拿目标窗口的焦点控件
# --------------------------------------------------------------------------


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", bc.wintypes.DWORD),
        ("flags", bc.wintypes.DWORD),
        ("hwndActive", bc.wintypes.HWND),
        ("hwndFocus", bc.wintypes.HWND),
        ("hwndCapture", bc.wintypes.HWND),
        ("hwndMenuOwner", bc.wintypes.HWND),
        ("hwndMoveSize", bc.wintypes.HWND),
        ("hwndCaret", bc.wintypes.HWND),
        ("rcCaret", bc.wintypes.RECT),
    ]


bc.user32.GetGUIThreadInfo.argtypes = [bc.wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
bc.user32.GetGUIThreadInfo.restype = bc.wintypes.BOOL


def _find_focus_window(hwnd: int) -> Optional[int]:
    """
    拿目标窗口所属线程的焦点子窗口。

    优先级：目标线程的 hwndFocus > 目标线程的 hwndActive > 本线程 GetFocus()
    都拿不到返回 None（调用方退回顶层窗口）。
    """
    # 先试目标窗口所在线程的 GUI 状态
    try:
        tid = bc.user32.GetWindowThreadProcessId(hwnd, None)
        if tid:
            gti = GUITHREADINFO()
            gti.cbSize = ctypes.sizeof(GUITHREADINFO)
            if bc.user32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
                if gti.hwndFocus and bc.user32.IsWindow(gti.hwndFocus):
                    return int(gti.hwndFocus)
                if gti.hwndActive and bc.user32.IsWindow(gti.hwndActive):
                    return int(gti.hwndActive)
    except Exception:
        pass

    # 兜底：本线程的 GetFocus（同进程窗口才有意义）
    try:
        f = bc.user32.GetFocus()
        if f and bc.user32.IsWindow(f):
            return int(f)
    except Exception:
        pass
    return None
