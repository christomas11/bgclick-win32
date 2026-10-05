#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bgclick.py —— Windows 后台窗口点击器（纯消息投递，不抢光标、不抢焦点）

原理：用 PostMessageW 把 WM_LBUTTONDOWN / WM_LBUTTONUP 投递到目标窗口的消息队列。
      光标位置、前台窗口、键盘焦点全都不动 —— 你可以同时干别的事。

用法速查
--------
  # 0) 出问题了？先跑诊断，它会把「为什么点不动」直接照出来
  python bgclick.py --doctor --title "xxx" --pos 400,300

  # 1) 先看有哪些窗口（拿句柄和标题）
  python bgclick.py --list

  # 2) 按窗口标题模糊匹配，点「客户区」左上角偏移 (400, 300) 的位置，点 3 次
  python bgclick.py --title "记事本" --pos 400,300 --count 3 --interval 1.0

  # 3) 目标是以管理员身份运行的 → 自己提权跑
  python bgclick.py --elevate --title "xxx" --pos 400,300
  #    或直接跑点击命令 —— 撞到权限墙时它会自己弹 UAC

  # 4) 用完整标题 + 进程名双重确认（更安全，避免误伤同名窗口）
  python bgclick.py --title "计算器" --exact --process ApplicationFrameHost.exe --center

  # 5) 虚跑一遍：只解析坐标、不发消息
  python bgclick.py --title "xxx" --pos 400,300 --dry-run

  # 6) 无限循环，每次 5 秒，偏移抖动 ±3 像素
  python bgclick.py --title "xxx" --pos 400,300 --count 0 --interval 5 --jitter 3

权限（重要！）
--------------
Windows 的 UIPI（用户界面特权隔离）规则：**只有完整性级别(IL) >= 目标 IL 的进程，
才能给目标窗口发消息**；低发高一律丢弃，PostMessageW 返回 FALSE + 错误码 5。

关键实测结论（别踩这个坑）：
  * UIPI 只放行少数「无害」消息（WM_NULL、WM_MOVE 等）。
    ★ 所以拿 WM_NULL 当探针永远是「通过」，纯假阳性 —— 本脚本用 WM_MOUSEMOVE 探针。
  * 鼠标消息（WM_MOUSEMOVE / WM_LBUTTONDOWN / WM_USER / WM_APP ...）一律走 UIPI 过滤。
  * 普通程序 = Medium。管理员提权 = High。
    ★ 如果本进程是 Low（受限/沙箱终端就长这样），
      它发不进**任何**普通程序 —— 这跟目标是谁无关，提权也救不了。
      解法是换普通 PowerShell / cmd / 双击运行。

自检与解决：
  ★ 程序自己会申请管理员权限 —— 这是默认行为
    第一次发消息就撞上 UIPI（错误码 5）时，它会自动弹 UAC 并在新的管理员
    窗口里继续跑，你不用手动加任何参数。
      --elevate       立刻提权重跑（不等检测）
      --no-elevate    关掉自动提权
    它不会乱弹 UAC：只有「提权确实能解决」时才弹
    （已提权 / 目标 IL 不比自己高 / 自己处在受限沙箱里 → 都不弹）。

  --doctor        只读诊断：自己 IL 多少、目标 IL 多少、卡在哪一层、该走哪条路
  --scan          一次列出本机哪些窗口可点、哪些被挡
  --method hardware  提权也点不动时的终极手段：真点击（会动光标，但基本万能）

点了没反应的排查顺序（从最常见的开始）
--------------------------------------
  1. 本进程在受限/沙箱环境（IL=Low）→ 换普通终端，这是最容易忽略的
  2. 目标程序是管理员权限（IL=High）→ 用 --elevate
  3. 坐标不对 → 先 --dry-run，或用 --center 排除坐标问题
  4. 点在子控件上 → 本脚本默认逐层探测，自动选最深的可投递窗口
  5. 程序不吃合成消息 → 换 --method send，或退到 --method hardware
  6. 需要窗口激活才响应 → 加 --activate

参数
----
  --title TEXT        窗口标题，默认子串匹配（不区分大小写）
  --regex             标题按正则匹配
  --exact             标题必须完全相等
  --process NAME      同时要求进程名（如 notepad.exe）
  --hwnd N            直接指定窗口句柄（十进制或 0x 开头）
  --index N           匹配多个窗口时选第 N 个（默认 0），配合 --list 用
  --pos X,Y           相对客户区左上角的偏移（X/Y 可写十进制，也接受 0x 前缀）
  --center            点客户区正中心（与 --pos 二选一）
  --screen-pos X,Y    屏幕绝对坐标
  --button            左键/右键/中键，默认 left
  --count N           点击次数，0 = 无限循环（默认 1）
  --interval SEC      间隔秒数（默认 0.5）
  --jitter N          每次点击的随机像素抖动，默认 0
  --method M          post（默认，纯后台）/ send / hardware（真点击，会动光标）
  --no-deep           不向下钻取子窗口，直接发给顶层窗口
  --activate          点击前先把窗口调到前台（默认关）
  --elevate           提权后重新运行（会弹 UAC）
  --doctor            只读诊断模式
  --dry-run           只打印将要点击的位置，不发消息
  --json              以 JSON 输出
  --keep-open         结束时等回车（提权重启后自动开）

退出码：0 成功 / 1 找不到窗口 / 2 参数错误 / 3 目标窗口提前关闭
        4 权限被拒（UIPI，需要 --elevate） / 5 消息投递失败

已知限制（不是 bug，是 Windows 的设计）
----------------------------------------
* 消息投递绕过真实输入队列，所以有些程序会「装死」：
  - 用 DirectX / Vulkan 独占渲染的游戏、部分 Unity 全屏程序
  - 用 Raw Input 或低级钩子自己读鼠标的软件
  - Chrome / Electron 的部分区域对合成消息不敏感
  这些情况可以试 --activate，或 --method hardware。
* 有些程序还会检查「鼠标是否真的在窗口上」，这种只能靠真点击。
* 无边框全屏窗口的客户区坐标 = 屏幕坐标，别把 --pos 当成全屏比例。
* PostMessageW 返回成功只代表**操作系统收下了这条消息**，
  不代表目标程序一定会响应 —— 这两件事必须分开看。

作者：Christina（本助手）  // c. 2026-10
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import random
import re
import struct
import subprocess
import sys
import time
from typing import Iterator, Optional

# --------------------------------------------------------------------------
# Win32 常量与函数声明
# --------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_MOUSEMOVE = 0x0200

MK_LBUTTON, MK_RBUTTON, MK_MBUTTON = 0x0001, 0x0002, 0x0010

BUTTONS = {
    "left": (WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON),
    "right": (WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON),
    "middle": (WM_MBUTTONDOWN, WM_MBUTTONUP, MK_MBUTTON),
}

# 真点击模式（SendInput 走鼠标事件队列）用的 flag
HW_BUTTONS = {
    "left": (0x0002, 0x0004),      # MOUSEEVENTF_LEFTDOWN / LEFTUP
    "right": (0x0008, 0x0010),     # RIGHTDOWN / RIGHTUP
    "middle": (0x0020, 0x0040),    # MIDDLEDOWN / MIDDLEUP
}

CWP_SKIPINVISIBLE, CWP_SKIPDISABLED = 0x0001, 0x0002

ERROR_ACCESS_DENIED = 5
ERROR_INVALID_WINDOW_HANDLE = 1400
ERROR_NOT_ENOUGH_QUOTA = 1816

SMTO_ABORTIFHUNG, SMTO_BLOCK = 0x0001, 0x0002

# --- 函数签名（64 位下必须显式声明，否则 LPARAM/句柄会被截断成 int32）---
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL

user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL

user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int

user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int

user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int

user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL

user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL

user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.restype = wintypes.BOOL

user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ScreenToClient.restype = wintypes.BOOL

user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL

user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)
]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM

user32.ChildWindowFromPointEx.argtypes = [wintypes.HWND, wintypes.POINT, wintypes.UINT]
user32.ChildWindowFromPointEx.restype = wintypes.HWND

user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND

user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL

user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND

user32.GetFocus.argtypes = []
user32.GetFocus.restype = wintypes.HWND

user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL

user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL

user32.mouse_event.argtypes = [
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p
]
user32.mouse_event.restype = None

user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE

kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

kernel32.GetCurrentProcess.argtypes = []
kernel32.GetCurrentProcess.restype = wintypes.HANDLE

advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
advapi32.OpenProcessToken.restype = wintypes.BOOL

advapi32.GetTokenInformation.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)
]
advapi32.GetTokenInformation.restype = wintypes.BOOL

shell32.IsUserAnAdmin.argtypes = []
shell32.IsUserAnAdmin.restype = wintypes.BOOL

shell32.ShellExecuteW.argtypes = [
    wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
    wintypes.LPCWSTR, ctypes.c_int
]
shell32.ShellExecuteW.restype = wintypes.HINSTANCE

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TokenElevation = 20
TokenIntegrityLevel = 25

# 完整性级别 RID -> 名字。UIPI 就是按这个等级做单向过滤的：
# 只有「同级或更高级」的进程才能给目标窗口发消息，低级发高级一律丢弃。
INTEGRITY_LEVELS = {
    0x0000: "Untrusted（不可信）",
    0x1000: "Low（低）",
    0x2000: "Medium（中，普通程序的标准级别）",
    0x3000: "High（高，管理员提权后的级别）",
    0x4000: "System（系统）",
    0x5000: "Protected（受保护进程）",
}

advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)

advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)


# ---- SID / 令牌相关结构体（定义在 _integrity_from_token 之前，避免靠"运行时才解析"）----

class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [("Label", SID_AND_ATTRIBUTES)]


# ---- EnumWindows 回调与函数签名（定义在 iter_top_level_windows 之前）----

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL


class AppError(Exception):
    """带退出码的业务异常。"""

    def __init__(self, msg: str, code: int = 2):
        super().__init__(msg)
        self.code = code


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


# --------------------------------------------------------------------------
# 找窗口
# --------------------------------------------------------------------------


def match_windows(args) -> list[dict]:
    """按命令行条件筛出候选窗口，返回 describe() 列表。"""
    if args.hwnd is not None:
        if not user32.IsWindow(args.hwnd):
            return []
        return [describe(args.hwnd)]

    title_pat = None
    if args.title:
        title_pat = re.compile(args.title if args.regex else re.escape(args.title),
                               0 if args.regex else re.IGNORECASE)

    proc_want = (args.process or "").lower()
    found = []
    for hwnd in iter_top_level_windows():
        if args.only_visible and not user32.IsWindowVisible(hwnd):
            continue
        title = window_title(hwnd)
        if title_pat is not None:
            if args.exact:
                if not args.regex and title != args.title:
                    continue
                if args.regex and not title_pat.fullmatch(title):
                    continue
            elif not title_pat.search(title):
                continue
        elif not proc_want:
            continue
        if proc_want and process_name(window_pid(hwnd)).lower() != proc_want:
            continue
        found.append(describe(hwnd))
    return found


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


def _integrity_from_token(token) -> Optional[int]:
    """从令牌读出完整性级别 RID（如 0x1000 = Low）。读不到返回 None。"""
    need = wintypes.DWORD(0)
    advapi32.GetTokenInformation(token, TokenIntegrityLevel, None, 0, ctypes.byref(need))
    if need.value == 0:
        return None
    buf = ctypes.create_string_buffer(need.value)
    if not advapi32.GetTokenInformation(token, TokenIntegrityLevel, buf, need.value,
                                        ctypes.byref(need)):
        return None
    label = ctypes.cast(buf, ctypes.POINTER(TOKEN_MANDATORY_LABEL)).contents
    sid = label.Label.Sid
    if not sid:
        return None
    cnt = advapi32.GetSidSubAuthorityCount(sid)
    if not cnt:
        return None
    n = cnt.contents.value
    if n == 0:
        return None
    sub = advapi32.GetSidSubAuthority(sid, n - 1)  # 最后一个子权威 = 完整性 RID
    return int(sub.contents.value) if sub else None


def self_integrity() -> Optional[int]:
    """本进程的完整性级别 RID。"""
    h = kernel32.GetCurrentProcess()
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        return _integrity_from_token(token)
    finally:
        kernel32.CloseHandle(token)


def process_integrity(pid: int) -> Optional[int]:
    """目标进程的完整性级别 RID。"""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            return _integrity_from_token(token)
        finally:
            kernel32.CloseHandle(token)
    finally:
        kernel32.CloseHandle(h)


def integrity_name(rid: Optional[int]) -> str:
    if rid is None:
        return "读不到"
    return INTEGRITY_LEVELS.get(rid, f"未知（RID 0x{rid:x}）")


def describe_error(err: int) -> str:
    table = {
        ERROR_ACCESS_DENIED: "拒绝访问 —— UIPI 拦截（目标权限比本进程高，需要 --elevate）",
        ERROR_INVALID_WINDOW_HANDLE: "无效窗口句柄 —— 窗口可能已经关了",
        ERROR_NOT_ENOUGH_QUOTA: "消息队列已满（目标进程可能卡死了）",
    }
    return table.get(err, f"Windows 错误码 {err}")


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
    return send_click_hardware(hwnd, x, y, args.button, restore_cursor=args.restore_cursor)


def parse_pair(text: str) -> tuple[int, int]:
    """
    解析 "X,Y"。int(..., 0) 表示接受十进制、0x / 0o / 0b 前缀。
    例：'400,300' / '0x190,0x12C' / '0o620,300'
    """
    try:
        a, b = text.replace("，", ",").split(",")
        return int(a.strip(), 0), int(b.strip(), 0)
    except Exception:
        raise argparse.ArgumentTypeError(f"坐标格式应为 X,Y（可带 0x 前缀），收到：{text!r}")


# --------------------------------------------------------------------------
# 截图（PrintWindow：后台抓图，不需要窗口在前台，也不抢焦点）
# --------------------------------------------------------------------------

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

PW_CLIENTONLY = 0x00000001
PW_RENDERFULLCONTENT = 0x00000002
DIB_RGB_COLORS = 0
BI_RGB = 0
SRCCOPY = 0x00CC0020

user32.GetWindowDC.argtypes = [wintypes.HWND]
user32.GetWindowDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL

gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
gdi32.GetDIBits.restype = ctypes.c_int


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def window_dimensions(hwnd: int, client_only: bool = False) -> tuple[int, int]:
    r = wintypes.RECT()
    if client_only:
        user32.GetClientRect(hwnd, ctypes.byref(r))
        return r.right - r.left, r.bottom - r.top
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


# HGDI_ERROR 是一个"伪句柄"，SelectObject 失败时返回它（等于 (HGDIOBJ)-1）
_HGDI_ERROR = ctypes.c_void_p(-1).value


def capture_window_rgb(hwnd: int, client_only: bool = False,
                       method: str = "auto") -> tuple[int, int, bytes, str]:
    """
    抓取窗口像素。返回 (宽, 高, RGB 字节(自上而下, 每行 w*3), 实际用的方法)。

    为什么用 PrintWindow：它让窗口**自己**把自己画到我们的内存 DC 上，
    所以窗口被别的窗口盖住、甚至不在前台，也照样能抓到 —— 而且不抢焦点。

    method:
      auto  —— 先试 PrintWindow(PW_RENDERFULLCONTENT)，失败退到普通 PrintWindow，
               再失败退到 BitBlt（这条要求窗口可见且未被遮挡）
      print —— 只用 PrintWindow
      bitblt—— 直接从窗口 DC 拷贝（最快，但被遮挡就抓到别人的画面）
    """
    w, h = window_dimensions(hwnd, client_only)
    if w <= 0 or h <= 0:
        raise AppError(f"窗口尺寸无效：{w}x{h}（窗口可能已最小化或已关闭）", code=1)
    if w > 20000 or h > 20000:
        raise AppError(f"窗口尺寸过大：{w}x{h}", code=2)

    src_dc = user32.GetWindowDC(hwnd)
    if not src_dc:
        raise AppError(f"GetWindowDC 失败：{ctypes.get_last_error()}", code=5)
    mem_dc = gdi32.CreateCompatibleDC(src_dc)
    bmp = gdi32.CreateCompatibleBitmap(src_dc, w, h)
    old_obj = gdi32.SelectObject(mem_dc, bmp)
    used = "none"

    # SelectObject 失败返回 NULL(0) 或 HGDI_ERROR((HGDIOBJ)-1)，两者都要当作"没换成功"
    if not old_obj or old_obj == _HGDI_ERROR:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, src_dc)
        raise AppError(f"SelectObject 失败：{ctypes.get_last_error()}", code=5)

    try:
        if method in ("auto", "print"):
            flags = PW_RENDERFULLCONTENT | (PW_CLIENTONLY if client_only else 0)
            ok = user32.PrintWindow(hwnd, mem_dc, flags)
            if ok:
                used = "printwindow-fullcontent"
            else:
                flags = PW_CLIENTONLY if client_only else 0
                if user32.PrintWindow(hwnd, mem_dc, flags):
                    used = "printwindow"
                elif method == "print":
                    raise AppError("PrintWindow 失败（窗口可能被保护或已最小化）", code=5)
        if used == "none":
            if not gdi32.BitBlt(mem_dc, 0, 0, w, h, src_dc, 0, 0, SRCCOPY):
                raise AppError(f"BitBlt 失败：{ctypes.get_last_error()}", code=5)
            used = "bitblt"

        # 取像素：32 位 BGRA，自下而上
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h          # 负数 = 自上而下，省一次翻转
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        got = gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)
        if got == 0:
            raise AppError(f"GetDIBits 失败：{ctypes.get_last_error()}", code=5)

        # BGRA -> RGB（顺手丢掉 alpha，它在这个场景里没用）
        raw = buf.raw[:w * h * 4]
        rgb = bytearray(w * h * 3)
        rgb[0::3] = raw[2::4]   # R
        rgb[1::3] = raw[1::4]   # G
        rgb[2::3] = raw[0::4]   # B
        return w, h, bytes(rgb), used
    finally:
        # old_obj 已在上面确认过是有效句柄，可以安全换回去
        if old_obj and old_obj != _HGDI_ERROR:
            gdi32.SelectObject(mem_dc, old_obj)
        if bmp:
            gdi32.DeleteObject(bmp)
        if mem_dc:
            gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, src_dc)


# --- 极简 PNG 编码（纯标准库 zlib，不依赖 Pillow）---

def _png_chunk(tag: bytes, data: bytes) -> bytes:
    import zlib as _z
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", _z.crc32(tag + data) & 0xFFFFFFFF))


def encode_png_rgb(width: int, height: int, rgb: bytes, level: int = 6) -> bytes:
    """把自上而下的 RGB 原始像素编码成 PNG 字节流。"""
    import zlib as _z
    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)                                  # 每行滤波器类型 0（None）
        raw += rgb[y * stride:(y + 1) * stride]

    ihdr = (struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))  # 8bit, truecolor RGB
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", _z.compress(bytes(raw), level))
            + _png_chunk(b"IEND", b""))


def encode_bmp_rgb(width: int, height: int, rgb: bytes) -> bytes:
    """编码成 BMP（有些老工具吃 BMP 不吃 PNG）。24 位，自下而上。"""
    stride = (width * 3 + 3) & ~3
    pad = stride - width * 3
    rows = bytearray()
    for y in range(height - 1, -1, -1):                # BMP 是自下而上
        row = rgb[y * width * 3:(y + 1) * width * 3]
        for i in range(0, len(row), 3):                # RGB -> BGR
            rows += bytes((row[i + 2], row[i + 1], row[i]))
        rows += b"\x00" * pad
    pixels = bytes(rows)
    header = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(pixels), 0, 0, 14 + 40)
    dib = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(pixels),
                      2835, 2835, 0, 0)
    return header + dib + pixels


def save_screenshot(hwnd: int, path: str, client_only: bool = False,
                    fmt: str = "png", method: str = "auto") -> dict:
    """抓图并存盘。返回元信息（含实际尺寸、格式、方法）。"""
    w, h, rgb, used = capture_window_rgb(hwnd, client_only=client_only, method=method)
    data = encode_bmp_rgb(w, h, rgb) if fmt.lower() == "bmp" else encode_png_rgb(w, h, rgb)

    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return {"path": os.path.abspath(path), "width": w, "height": h,
            "format": fmt.lower(), "bytes": len(data), "capture_method": used,
            "client_only": client_only}


# --------------------------------------------------------------------------
# 提权
# --------------------------------------------------------------------------


def _python_for_elevation() -> str:
    """
    挑一个「有控制台」的 Python 来跑提权后的实例。
    如果本进程是 pythonw.exe（无控制台），提权后看不到任何输出，
    所以换成同目录的 python.exe。
    """
    exe = sys.executable
    base = os.path.basename(exe).lower()
    if base.startswith("pythonw"):
        cand = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(cand):
            return cand
    return exe


def _elevation_command(extra_args: list[str]) -> tuple[str, str]:
    """组装提权后要执行的 (可执行文件, 参数字符串)。"""
    argv = [a for a in extra_args if a not in ("--elevate", "--_elevated")]
    argv.append("--_elevated")          # 防重入标记：子进程看到它就不再提权
    if "--keep-open" not in argv:
        argv.append("--keep-open")      # 新控制台跑完会秒关，得留住给用户看结果

    if getattr(sys, "frozen", False):
        # PyInstaller 打好的 exe：直接拿自己当可执行文件
        return sys.executable, subprocess.list2cmdline(argv)

    script = os.path.abspath(__file__)
    return _python_for_elevation(), subprocess.list2cmdline([script] + argv)


def relaunch_as_admin(extra_args: list[str], verbose: bool = True) -> int:
    """
    用 ShellExecuteW 的 "runas" 动词重新拉起自己 —— 这就是「程序本体申请管理员权限」。

    提权后的进程在一个新的管理员控制台里跑，原进程立刻退出。
    返回 0 表示已成功拉起（调用方应直接结束），非 0 表示提权失败。
    """
    exe, params = _elevation_command(extra_args)
    cwd = os.getcwd()

    if verbose:
        print("正在申请管理员权限…… 屏幕上会弹 UAC，请点「是」。")

    try:
        rc = shell32.ShellExecuteW(None, "runas", exe, params, cwd, 1)
    except Exception as e:  # 极少数环境会直接抛
        print(f"提权调用失败：{e}", file=sys.stderr)
        return 5

    code = int(rc)
    if code <= 32:
        # ShellExecuteW 返回值 <= 32 都是错误码，不是进程 ID
        if code == ERROR_ACCESS_DENIED:
            print("提权被拒绝（UAC 里点了「否」，或策略不允许）。", file=sys.stderr)
        elif code == 2:
            print("提权失败：找不到可执行文件（Python 路径异常）。", file=sys.stderr)
        else:
            print(f"提权失败，ShellExecuteW 返回 {code}。", file=sys.stderr)
        print("备选方案：右键「以管理员身份运行」打开 PowerShell / cmd，再手动跑本脚本。",
              file=sys.stderr)
        return 5

    if verbose:
        print("已在新窗口以管理员权限启动，本窗口可以关了。")
    return 0


def elevation_would_help(hwnd: int) -> Optional[bool]:
    """
    提权有没有用？比较自己和目标的完整性级别。

    True  = 目标 IL 更高，提权大概率能解决
    False = 自己 IL 已经 >= 目标，提权没意义（该换 --method 或修坐标）
    None  = 读不到目标 IL，说不准
    """
    me = self_integrity()
    tgt = process_integrity(window_pid(hwnd))
    if me is None or tgt is None:
        return None
    return me < tgt


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bgclick.py",
        description="对指定窗口后台点击（PostMessage 投递，不抢光标/焦点）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--list", action="store_true", help="列出可见窗口后退出")
    p.add_argument("--list-all", action="store_true", help="列出全部顶层窗口（含隐藏）后退出")
    p.add_argument("--doctor", action="store_true", help="只读诊断：为什么点不动")
    p.add_argument("--doctor-click", action="store_true",
                   help="诊断时额外真投递一次（会真的点目标一下，慎用）")
    p.add_argument("--scan", action="store_true",
                   help="扫描所有窗口，列出哪些可投递、哪些被 UIPI 拦截")
    p.add_argument("--title", help="窗口标题（默认子串匹配，忽略大小写）")
    p.add_argument("--regex", action="store_true", help="标题按正则匹配")
    p.add_argument("--exact", action="store_true", help="标题必须完全相等")
    p.add_argument("--process", help="同时要求进程名，如 notepad.exe")
    p.add_argument("--hwnd", type=lambda s: int(s, 0), help="直接指定窗口句柄")
    p.add_argument("--index", type=int, default=0, help="匹配到多个窗口时选第几个（默认 0）")
    p.add_argument("--only-visible", action="store_true", default=True,
                   help="只匹配可见窗口（默认开）")
    p.add_argument("--include-hidden", dest="only_visible", action="store_false",
                   help="把隐藏窗口也纳入匹配")

    g = p.add_mutually_exclusive_group()
    g.add_argument("--pos", type=parse_pair, help="客户区偏移坐标 X,Y（可带 0x 前缀）")
    g.add_argument("--center", action="store_true", help="点客户区正中心")
    g.add_argument("--screen-pos", type=parse_pair, help="屏幕绝对坐标 X,Y")

    p.add_argument("--button", choices=list(BUTTONS), default="left", help="鼠标键（默认 left）")
    p.add_argument("--count", type=int, default=1, help="点击次数，0 = 无限循环（默认 1）")
    p.add_argument("--interval", type=float, default=0.5, help="间隔秒数（默认 0.5）")
    p.add_argument("--jitter", type=int, default=0, help="每次点击的随机像素抖动（默认 0）")
    p.add_argument("--method", choices=["post", "send", "hardware"], default="post",
                   help="投递方式（默认 post 纯后台）")
    p.add_argument("--no-deep", dest="deep", action="store_false", default=True,
                   help="不向下钻取子窗口，直接发给顶层窗口")
    p.add_argument("--restore-cursor", action="store_true",
                   help="hardware 模式下点完把光标放回原处")
    p.add_argument("--activate", action="store_true", help="点击前先把窗口调到前台")
    p.add_argument("--elevate", action="store_true",
                   help="立刻申请管理员权限重跑（不等检测，会弹 UAC）")
    p.add_argument("--no-elevate", dest="auto_elevate", action="store_false", default=True,
                   help="禁止自动提权（默认：检测到权限不够就自己弹 UAC）")
    p.add_argument("--_elevated", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--force-continue", action="store_true",
                   help="即使投递失败也继续循环（默认失败即停）")
    p.add_argument("--shot", help="截图并存到指定路径（PNG；后缀 .bmp 则存 BMP）")
    p.add_argument("--client-only", action="store_true",
                   help="只截客户区，不含标题栏和边框")
    p.add_argument("--shot-method", choices=["auto", "print", "bitblt"], default="auto",
                   help="截图方式（默认 auto：PrintWindow 优先，可抓被遮挡的窗口）")
    p.add_argument("--shot-dir", default="shots",
                   help="截图默认输出目录（默认 ./shots）")
    p.add_argument("--dry-run", action="store_true", help="只打印坐标，不发消息")
    p.add_argument("--json", action="store_true", help="JSON 输出")
    p.add_argument("--keep-open", action="store_true", help="结束时等回车")
    return p


def cmd_list(args) -> int:
    rows = []
    for hwnd in iter_top_level_windows():
        if not args.list_all and not user32.IsWindowVisible(hwnd):
            continue
        title = window_title(hwnd)
        if not args.list_all and not title:
            continue
        rows.append(describe(hwnd))
    rows.sort(key=lambda r: (not r["visible"], r["process"], r["title"]))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    me = "管理员" if is_admin() else "普通用户"
    print(f"共 {len(rows)} 个窗口（本进程权限：{me}）\n")
    print(f"{'句柄(hex)':<14}{'管理员':<8}{'进程':<24}{'客户区':<12}标题")
    print("-" * 104)
    for r in rows:
        cw, ch = r["client_size"]
        elev = process_is_elevated(r["pid"])
        flag = "是" if elev else ("否" if elev is False else "?")
        print(f"{r['hwnd_hex']:<14}{flag:<8}{r['process'][:22]:<24}"
              f"{str(cw) + 'x' + str(ch):<12}{r['title'][:46]}")
    return 0


def cmd_doctor(args) -> int:
    """只读诊断：把「为什么点不动」直接照出来。"""
    lines: list[str] = []

    def say(s=""):
        lines.append(s)

    say("=" * 68)
    say("bgclick 诊断报告")
    say("=" * 68)
    admin = is_admin()
    self_il = self_integrity()
    say(f"[1] 本进程权限       : {'管理员 (已提权)' if admin else '普通用户 (未提权)'}")
    say(f"    完整性级别(IL)   : {integrity_name(self_il)}"
        + ("   ★ 受限沙箱！" if self_il is not None and self_il < 0x2000 else ""))
    if self_il is not None and self_il < 0x2000:
        say("    警告             : IL 低于 Medium，按 UIPI 规则**发不进任何普通程序**。")
        say("                       这通常是沙箱/受限环境导致的，跟目标程序无关。")
        say("                       请在普通 PowerShell（或提权窗口）里跑本脚本。")
    say(f"    DPI 感知         : {enable_dpi_awareness() or '未设置（坐标可能有偏差）'}")
    say(f"    Python           : {sys.version.split()[0]}  {8 * ctypes.sizeof(ctypes.c_void_p)} 位")

    if not args.title and args.hwnd is None and not args.process:
        say("")
        say("没给 --title / --hwnd / --process，只能报告本进程权限。")
        say("补上目标窗口信息再跑一次，比如：")
        say('  python bgclick.py --doctor --title "xxx" --pos 400,300')
        print("\n".join(lines))
        return 0

    cands = match_windows(args)
    if not cands:
        say("")
        say("[!] 没找到匹配的窗口。先跑 --list 确认标题。")
        print("\n".join(lines))
        return 1

    say("")
    say(f"[2] 匹配到 {len(cands)} 个窗口" + (f"，本次分析 --index {args.index}" if len(cands) > 1 else ""))
    for i, c in enumerate(cands[:8]):
        say(f"    {'*' if i == args.index else ' '}[{i}] {c['hwnd_hex']:<12}"
            f"{c['process'][:20]:<22}{c['client_size'][0]}x{c['client_size'][1]:<8}{c['title'][:36]}")

    if args.index >= len(cands):
        say(f"[!] --index {args.index} 越界")
        print("\n".join(lines))
        return 2

    t = cands[args.index]
    hwnd = t["hwnd"]
    elev = process_is_elevated(t["pid"])
    tgt_il = process_integrity(t["pid"])
    say("")
    say(f"[3] 目标进程权限     : "
        + ("管理员 (已提权)" if elev else "普通用户" if elev is False else "读不到（可能是受保护进程）"))
    say(f"    目标完整性级别   : {integrity_name(tgt_il)}")
    say(f"    窗口类名         : {t['class']}")

    # UIPI 是「单向过滤器」：IL(自己) >= IL(目标) 才发得进去
    verdict_permission = None
    if self_il is not None and tgt_il is not None:
        if self_il < tgt_il:
            verdict_permission = (
                f"UIPI 必定拦截：本进程 IL={integrity_name(self_il)} < "
                f"目标 IL={integrity_name(tgt_il)}。"
                + ("本进程在受限沙箱里，请换普通终端跑。" if self_il < 0x2000 else "需要 --elevate。")
            )
        elif self_il >= tgt_il:
            verdict_permission = None  # 权限 OK
    elif elev is True and not admin:
        verdict_permission = ("目标是管理员，本进程是普通用户。→ 必须用 --elevate")
    elif elev is None and not admin:
        verdict_permission = "读不到目标完整性级别，无法排除 UIPI。若点不动就试 --elevate"

    # 坐标解析
    cw, ch = t["client_size"]
    point = None
    if args.center:
        point = (cw // 2, ch // 2)
    elif args.pos:
        point = args.pos
    elif args.screen_pos:
        point = resolve_client_point(hwnd, *args.screen_pos)
    say("")
    if point is None:
        say("[4] 坐标             : 未指定（--pos / --center / --screen-pos 都没给）")
    else:
        inrange = 0 <= point[0] < cw and 0 <= point[1] < ch
        say(f"[4] 客户区           : {cw} x {ch}")
        say(f"    点击点           : {point}   {'在客户区内 ✔' if inrange else '★ 超出客户区 ✘'}")
        target, tx, ty, path = deepest_child_at(hwnd, *point)
        say(f"    命中窗口         : {hex(target)}  类名 {window_class(target)}")
        say(f"    钻取路径         : {' > '.join(path)}")
        if target != hwnd:
            say(f"    子窗口内坐标     : ({tx}, {ty})  ← 实际会发给它")
            say("    说明             : 命中了子控件，这通常是对的（标准控件由子窗口自己处理鼠标）")

    # ★ 权限探针：逐层探到子控件（只探顶层会假阳性！）
    say("")
    probe = None
    if point is None:
        probe = probe_window(hwnd)
        if probe["ok"]:
            say("[5] 权限探针         : 顶层通过 ✔")
        else:
            say(f"[5] 权限探针         : 顶层被拦 ✘  错误码 {probe['error']}")
            say(f"    原因             : {describe_error(probe['error'])}")
    else:
        hier = probe_hierarchy(hwnd, *point)
        say("[5] 权限探针         : 逐层检测（探针用 WM_MOUSEMOVE，与真实点击同源）")
        for i, c in enumerate(hier["chain"]):
            indent = "    " + "  " * i + ("└ " if i else "")
            flag = "通过 ✔" if c["ok"] else f"被拦 ✘ (错误码 {c['error']})"
            say(f"{indent}{c['hwnd_hex']:<12}{c['class'][:26]:<28}{flag}")
        if hier["blocked"]:
            b = hier["blocked"][0]
            say("")
            say(f"    ★ 拦截点在 {b['hwnd_hex']}（类名 {b['class']}）")
            say(f"      原因：{describe_error(b['error'])}")
            if hier["best"] != hwnd:
                say(f"      可退守到 {hex(hier['best'])}（{window_class(hier['best'])}），"
                    "点击会自动发给它")
            else:
                say("      连顶层都进不去，只能提权或改用 hardware")
            probe = {"ok": False, "error": b["error"]}
        else:
            probe = {"ok": True, "error": 0}

    # 真投递测试（默认不做；诊断默认只读，不误触目标程序）
    say("")
    if not args.doctor_click:
        say("[6] 实投测试         : 已跳过（默认只读探针；要真点一次加 --doctor-click）")
    elif point is None:
        say("[6] 实投测试         : 已跳过（没给坐标）")
    else:
        real = (resolving_click(hwnd, *point, args) if args.deep
                else do_click(hwnd, *point, args))
        if real["ok"]:
            via = {"post": "PostMessageW", "send": "SendMessageTimeoutW",
                   "hardware": "SetCursorPos+mouse_event"}[real["method"]]
            say(f"[6] 实投测试         : {via} 返回成功 ✔  实际发给 {hex(real['hwnd'])}")
            if real.get("fallback_from"):
                say(f"    退守             : 原本命中 {real['fallback_from']} 被拦，已退到父窗口")
            say("    注意             : 这只代表操作系统收下了消息，")
            say("                       不代表目标程序一定会响应")
        else:
            say(f"[6] 实投测试         : 失败 ✘  错误码 {real['error']}")
            say(f"    原因             : {describe_error(real['error'])}")

    # 判定
    say("")
    say("[7] 判定与建议")
    blocked = not probe["ok"]
    if verdict_permission:
        say(f"    ★ {verdict_permission}")
    if blocked:
        if self_il is not None and self_il < 0x2000:
            say("    ★ 根因：本进程完整性级别低于 Medium（受限/沙箱环境）")
            say("      UIPI 会拦下它发给**任何**普通程序的消息 —— 跟目标是谁无关。")
            say("      解法：换普通 PowerShell / cmd / 双击运行，别在沙箱终端里跑。")
            say("      注意：这种环境下 --elevate 多半也会被挡，优先换终端。")
        elif not admin:
            say("    ★ 消息被 UIPI 拦下了。按这个顺序解决：")
            say("      1) 加 --elevate 提权重跑（会弹 UAC）——最直接")
            say("      2) 或加 --elevate 立刻提权")
            say("      3) 若提权后仍被拦，说明目标在更高保护级别")
            say("         （受保护进程 / AppContainer / 跨会话窗口），")
            say("         PostMessage 这条路走不通，只能改用 --method hardware 真点击")
        else:
            say("    ★ 本进程已是管理员仍被拦 —— 目标保护级别更高")
            say("      （受保护进程 / AppContainer 沙箱 / 不同会话）")
            say("      建议：--method hardware 真点击（会动光标，但基本万能）")
        say("    另查：--scan 可以一次看出本机哪些窗口可点、哪些被挡")
    else:
        say("    权限层面没问题，消息能被目标接收入队 ✔")
        say("    若程序仍无反应，按这个顺序试：")
        say("      1) --method send       （某些程序忽略 Post，只吃同步消息）")
        say("      2) --activate          （需要窗口激活才响应）")
        say("      3) --method hardware   （真点击，基本万能，代价是动光标）")
        say("      4) 换坐标：--center 或调整 --pos")
        say("      5) 目标若是独占全屏游戏 / Electron，合成消息可能天然无效")
    say("=" * 68)

    if args.json:
        print(json.dumps({"lines": lines, "target": t, "elevated_self": admin,
                          "elevated_target": elev, "uipi_probe_ok": probe["ok"],
                          "uipi_error": probe["error"]},
                         ensure_ascii=False, indent=2))
    else:
        print("\n".join(lines))
    return 4 if blocked else 0


def cmd_scan(args) -> int:
    """
    扫描所有可见窗口，用 WM_MOUSEMOVE 探针判定每个窗口能不能接收本进程的消息。
    只读，无副作用。用来快速找出 --elevate 是否值得。
    """
    rows = []
    for hwnd in iter_top_level_windows():
        if not user32.IsWindowVisible(hwnd):
            continue
        title = window_title(hwnd)
        if not title:
            continue
        pr = probe_window(hwnd)
        pid = window_pid(hwnd)
        rows.append({
            "hwnd": hwnd, "hwnd_hex": hex(hwnd), "title": title,
            "process": process_name(pid), "pid": pid,
            "elevated": process_is_elevated(pid),
            "integrity": process_integrity(pid),
            "postable": pr["ok"], "error": pr["error"],
            "client_size": describe(hwnd, with_process=False)["client_size"],
        })

    ok_n = sum(1 for r in rows if r["postable"])
    blocked = [r for r in rows if not r["postable"]]
    self_il = self_integrity()

    if args.json:
        print(json.dumps({"self_elevated": is_admin(), "self_integrity": self_il,
                          "total": len(rows), "postable": ok_n, "blocked": blocked},
                         ensure_ascii=False, indent=2))
        return 0

    me = "管理员" if is_admin() else "普通用户"
    print(f"本进程权限：{me}，完整性级别：{integrity_name(self_il)}")
    if self_il is not None and self_il < 0x2000:
        print("★ 警告：IL 低于 Medium（受限沙箱）。UIPI 会拦下发给所有普通程序的消息，")
        print("  这跟目标是谁无关 —— 请换普通 PowerShell / cmd 跑，别在沙箱终端里跑。")
    print(f"可见窗口 {len(rows)} 个：{ok_n} 个可投递，{len(blocked)} 个被 UIPI 拦截\n")
    print(f"{'状态':<8}{'句柄(hex)':<13}{'目标IL':<26}{'进程':<24}{'客户区':<12}标题")
    print("-" * 116)
    for r in sorted(rows, key=lambda r: (r["postable"], r["process"])):
        flag = "可投递" if r["postable"] else "被拦截"
        cw, ch = r["client_size"]
        print(f"{flag:<8}{r['hwnd_hex']:<13}{integrity_name(r['integrity'])[:24]:<26}"
              f"{r['process'][:22]:<24}{str(cw) + 'x' + str(ch):<12}{r['title'][:36]}")

    if blocked:
        print(f"\n被拦截的 {len(blocked)} 个窗口，错误码分布：")
        dist: dict[int, int] = {}
        for r in blocked:
            dist[r["error"]] = dist.get(r["error"], 0) + 1
        for err, n in sorted(dist.items()):
            print(f"  错误码 {err} x{n}  —— {describe_error(err)}")
        if not is_admin():
            print("\n★ 想点这些窗口，可以让程序自己提权：")
            print("    python bgclick.py --elevate --title \"xxx\" --pos X,Y")
            print("  或者直接跑点击命令，它撞到权限墙时会自己申请管理员权限。")
        else:
            print("\n★ 本进程已是管理员，仍被拦说明目标保护级别更高，"
                  "PostMessage 走不通，改用 --method hardware。")
    return 0


def cmd_click(args) -> int:
    if args.count < 0:
        raise AppError("--count 不能为负数")
    if not (args.pos or args.center or args.screen_pos):
        raise AppError("必须指定 --pos / --center / --screen-pos 之一（-h 看帮助）")

    cands = match_windows(args)
    if not cands:
        raise AppError("没找到匹配的窗口。跑一下 --list 看看？", code=1)
    if args.index >= len(cands) or args.index < 0:
        msg = [f"匹配到 {len(cands)} 个窗口，--index {args.index} 越界"]
        msg += [f"  [{i}] {c['hwnd_hex']} {c['process']} | {c['title']}"
                for i, c in enumerate(cands)]
        raise AppError("\n".join(msg))

    target_info = cands[args.index]
    hwnd = target_info["hwnd"]
    cw, ch = target_info["client_size"]

    if len(cands) > 1:
        print(f"注意：匹配到 {len(cands)} 个窗口，这次用的是 [{args.index}]。"
              f"想换一个就加 --index N：", file=sys.stderr)
        for i, c in enumerate(cands[:8]):
            mark = "*" if i == args.index else " "
            print(f"  {mark}[{i}] {c['hwnd_hex']:<12}{c['process'][:20]:<22}"
                  f"{c['client_size'][0]}x{c['client_size'][1]:<8}{c['title'][:40]}",
                  file=sys.stderr)

    if args.center:
        base = (cw // 2, ch // 2)
    elif args.pos:
        base = args.pos
    else:
        base = resolve_client_point(hwnd, *args.screen_pos)

    if not (0 <= base[0] < cw and 0 <= base[1] < ch):
        print(f"警告：基准坐标 {base} 落在客户区 {cw}x{ch} 之外，程序多半收不到这个点击",
              file=sys.stderr)

    meta = {
        "hwnd": hwnd,
        "hwnd_hex": target_info["hwnd_hex"],
        "title": target_info["title"],
        "process": target_info["process"],
        "client_size": [cw, ch],
        "base_pos": list(base),
        "button": args.button,
        "method": args.method,
        "count": args.count,
        "interval": args.interval,
        "elevated": is_admin(),
        "dry_run": args.dry_run,
    }
    if not args.json:
        print(f"本进程权限：{'管理员' if is_admin() else '普通用户'}"
              + ("" if is_admin() else "（目标若是管理员权限，需要 --elevate）"))
        print(f"目标：{target_info['hwnd_hex']}  {target_info['process']}  "
              f"「{target_info['title']}」")
        print(f"客户区 {cw}x{ch}，基准坐标 {base}，{args.button} 键，方式 {args.method}，"
              f"{'无限循环' if args.count == 0 else str(args.count) + ' 次'}，"
              f"间隔 {args.interval}s")
        if args.dry_run:
            print("（dry-run：不发消息）")

    if args.activate and not args.dry_run and args.method != "hardware":
        if not user32.SetForegroundWindow(hwnd):
            print("提示：SetForegroundWindow 被系统拒绝（前台锁），继续尝试投递。",
                  file=sys.stderr)
        time.sleep(0.15)

    done = 0
    failed = 0
    while args.count == 0 or done < args.count:
        if not user32.IsWindow(hwnd):
            print(f"\n目标窗口已关闭，停止。（已点击 {done} 次）", file=sys.stderr)
            return 3

        x, y = base
        if args.jitter:
            x += random.randint(-args.jitter, args.jitter)
            y += random.randint(-args.jitter, args.jitter)
        x, y = max(0, min(x, cw - 1)), max(0, min(y, ch - 1))

        # 先算出实际会命中的窗口（dry-run 只算不发）
        hier = (probe_hierarchy(hwnd, x, y)
                if args.deep and args.method != "hardware" else None)

        if args.dry_run:
            hit = hier["best"] if hier else hwnd
            will_fallback = bool(hier and hier["blocked"] and hier["best"] != hwnd)
            rec = {"method": "dry-run", "hwnd": hit,
                   "point": list(hier["best_point"]) if hier else [x, y],
                   "ok": True, "error": 0, "will_fallback": will_fallback}
            if hier:
                rec["hierarchy"] = [c["hwnd_hex"] for c in hier["chain"]]
                if hier["blocked"]:
                    rec["blocked_at"] = hier["blocked"][0]["hwnd_hex"]
        else:
            # 智能投递：逐层探测，选最深且可投递的窗口
            if args.deep and args.method != "hardware":
                rec = resolving_click(hwnd, x, y, args)
            else:
                rec = do_click(hwnd, x, y, args)
                rec.setdefault("hierarchy", [hex(hwnd)])

        rec.update({"index": done, "client_point": [x, y], **meta})

        if args.dry_run:
            if args.json:
                print(json.dumps(rec, ensure_ascii=False))
            else:
                chain = rec.get("hierarchy")
                extra = f"  层级 {' > '.join(chain)}" if chain else ""
                if rec.get("blocked_at"):
                    if rec.get("will_fallback"):
                        warn = f"  ★ {rec['blocked_at']} 被拦，会退守到 {hex(rec['hwnd'])}"
                    else:
                        warn = f"  ★ {rec['blocked_at']} 被拦且无可退守层（需提权）"
                else:
                    warn = ""
                print(f"[{done + 1}] 将点击客户区 ({x}, {y})，实际发给 {hex(rec['hwnd'])}"
                      f"{extra}{warn}")
        elif rec["ok"]:
            if args.json:
                print(json.dumps(rec, ensure_ascii=False))
            else:
                hit = rec["hwnd"]
                extra = f" -> 子窗口 {hex(hit)}" if hit != hwnd else ""
                if rec.get("fallback_from"):
                    extra += f"（{rec['fallback_from']} 被拦，已退守）"
                print(f"[{done + 1}] 已投递 ({x}, {y}){extra}"
                      + ("  [真点击]" if args.method == "hardware" else ""), flush=True)
        else:
            failed += 1
            err = rec["error"]
            print(f"[{done + 1}] 投递失败！{describe_error(err)}", file=sys.stderr)
            if args.json:
                print(json.dumps(rec, ensure_ascii=False))
            if err == ERROR_ACCESS_DENIED:
                where = rec.get("blocked_at")
                if where:
                    print(f"\n>>> 拦截点：{where['hwnd_hex']}（类名 {where['class']}）",
                          file=sys.stderr)
                # ★ 第一条消息就撞权限墙 → 直接自动提权重跑，别让用户手动加参数
                if done == 0 and not is_admin():
                    code = maybe_auto_elevate(
                        args, hwnd, f"给 {where['hwnd_hex'] if where else hex(hwnd)} 发消息被拒（UIPI）"
                    )
                    if code is not None:
                        return code
                if not is_admin():
                    print(">>> 这是 UIPI：本进程权限不够，消息被系统丢掉了。", file=sys.stderr)
                    print(">>> 加 --elevate 立刻提权重跑（会弹 UAC）",
                          file=sys.stderr)
                    print(">>> 先跑 --doctor 可以看清是顶层还是子控件被拦。", file=sys.stderr)
                    return 4
                print(">>> 已是管理员仍被拦，目标保护级别更高"
                      "（受保护进程 / AppContainer / 跨会话）。", file=sys.stderr)
                print(">>> PostMessage 这条路走不通，改用 --method hardware 真点击。",
                      file=sys.stderr)
                return 4
            if not args.force_continue:
                print(">>> 已停止。想无视失败继续循环就加 --force-continue",
                      file=sys.stderr)
                return 5

        done += 1
        if args.count == 0 or done < args.count:
            time.sleep(max(0.0, args.interval))

    if not args.json:
        tail = f"完成，共 {done} 次。" if failed == 0 else f"完成，共 {done} 次，其中 {failed} 次失败。"
        print(tail)
    return 0 if failed == 0 else 5


def maybe_auto_elevate(args, hwnd: int, reason: str) -> Optional[int]:
    """
    需要时自动提权重跑。返回退出码（表示"已经处理完了，别继续"）或 None（继续跑）。

    只有在「提权确实能解决问题」时才动手 —— 免得平白弹 UAC 骚扰用户：
      * 已经是管理员 → 不提
      * --no-elevate → 不提
      * 已经是被提权拉起的子进程（--_elevated）→ 不提，防止无限套娃
      * 目标 IL <= 自己 IL → 提了也没用，不提
    """
    if not getattr(args, "auto_elevate", True):
        return None
    if args._elevated:
        return None                      # 防重入
    if is_admin():
        return None
    if args.json:                        # JSON 模式别提权，否则调用方拿不到输出
        return None

    # 受限沙箱（IL < Medium）：提权基本也无效，别白弹 UAC 骚扰用户
    me = self_integrity()
    if me is not None and me < 0x2000:
        print(f"\n检测到权限不足：{reason}", file=sys.stderr)
        print(f"★ 但本进程完整性级别是 {integrity_name(me)}（受限沙箱），"
              "UIPI 会拦下它发给所有普通程序的消息。", file=sys.stderr)
        print("  提权在这种环境下通常也无效 —— 请换普通 PowerShell / cmd 重跑。",
              file=sys.stderr)
        return None

    helps = elevation_would_help(hwnd)
    if helps is False:
        return None                      # 权限已经够了，问题在别处

    print(f"\n检测到权限不足：{reason}", file=sys.stderr)
    if helps is True:
        print("目标完整性级别高于本进程 —— 提权可以解决。", file=sys.stderr)
    else:
        print("读不到目标完整性级别，但仍值得试一次提权。", file=sys.stderr)
    print("（不想自动提权就加 --no-elevate）", file=sys.stderr)

    return relaunch_as_admin(getattr(args, "_argv", []))


def interactive_guide() -> int:
    """
    没有任何参数、也没指定目标时（典型场景：双击脚本）的引导流程。
    """
    admin = is_admin()
    il = self_integrity()
    print("=" * 66)
    print("bgclick —— Windows 后台点击器")
    print("=" * 66)
    print(f"当前权限：{'管理员' if admin else '普通用户'}，"
          f"完整性级别：{integrity_name(il)}")
    if il is not None and il < 0x2000:
        print("★ 警告：完整性级别低于 Medium（受限/沙箱环境）")
        print("  UIPI 会拦下发给所有普通程序的消息 —— 请换普通 PowerShell / cmd 运行。")
    print()
    print("用法：")
    print('  python bgclick.py --list                                  看窗口列表')
    print('  python bgclick.py --scan                                  看哪些能点')
    print('  python bgclick.py --doctor --title "xxx" --pos 100,100    诊断点不动的原因')
    print('  python bgclick.py --title "xxx" --pos 400,300             点 1 次')
    print('  python bgclick.py --title "xxx" --center --count 0        无限循环点中心')
    print()
    print("不需要手动提权：撞到权限墙时程序会自己弹 UAC。")
    print("参数详解：python bgclick.py -h")
    print("=" * 66)

    # 有控制台（双击场景）就顺手列出窗口，省得再问一次
    try:
        stdin_tty = sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        stdin_tty = False
    if stdin_tty:
        print("\n当前可见窗口：")
        rows = []
        for hwnd in iter_top_level_windows():
            if not user32.IsWindowVisible(hwnd):
                continue
            title = window_title(hwnd)
            if title:
                rows.append(describe(hwnd))
        rows.sort(key=lambda r: r["process"])
        for i, r in enumerate(rows[:15]):
            cw, ch = r["client_size"]
            ok = probe_window(r["hwnd"])["ok"]
            print(f"  {'✔' if ok else '✘'} {r['hwnd_hex']:<12}{r['process'][:20]:<22}"
                  f"{cw}x{ch:<8}{r['title'][:36]}")
        if len(rows) > 15:
            print(f"  …… 还有 {len(rows) - 15} 个，用 --list 看全部")
        print("\n✔ = 能投递   ✘ = 被 UIPI 拦截")
    return 0


def cmd_shot(args) -> int:
    """截图子命令：抓指定窗口存成图片。"""
    cands = match_windows(args)
    if not cands:
        raise AppError("没找到匹配的窗口。跑一下 --list 看看？", code=1)
    if args.index >= len(cands) or args.index < 0:
        msg = [f"匹配到 {len(cands)} 个窗口，--index {args.index} 越界"]
        msg += [f"  [{i}] {c['hwnd_hex']} {c['process']} | {c['title']}"
                for i, c in enumerate(cands)]
        raise AppError("\n".join(msg))

    target = cands[args.index]
    hwnd = target["hwnd"]

    if len(cands) > 1:
        print(f"注意：匹配到 {len(cands)} 个窗口，用的是 [{args.index}]："
              f"{target['hwnd_hex']} {target['title']}", file=sys.stderr)

    path = args.shot
    fmt = "bmp" if path.lower().endswith(".bmp") else "png"
    if not os.path.splitext(path)[1]:
        path += "." + fmt

    if user32.IsIconic(hwnd):
        print("提示：窗口处于最小化状态。PrintWindow 可能会拿到黑图，"
              "建议先还原窗口。", file=sys.stderr)

    info = save_screenshot(hwnd, path, client_only=args.client_only,
                           fmt=fmt, method=args.shot_method)
    info.update({"hwnd": target["hwnd_hex"], "title": target["title"],
                 "process": target["process"]})

    if args.json:
        print(json.dumps(info, ensure_ascii=False))
    else:
        print(f"已截图：{info['path']}")
        print(f"  窗口 {target['hwnd_hex']}  {target['process']}  「{target['title']}」")
        print(f"  尺寸 {info['width']}x{info['height']}  {info['format'].upper()}  "
              f"{info['bytes']} 字节  方式 {info['capture_method']}"
              + ("  仅客户区" if info["client_only"] else ""))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    args._argv = argv                     # 留给自动提权用
    enable_dpi_awareness()

    # 显式 --elevate：立刻提权重跑
    if args.elevate and not args._elevated and not is_admin():
        return relaunch_as_admin(argv)
    if args.elevate and is_admin() and not args._elevated and not args.json:
        print("已经是管理员权限，直接开跑。")
    elif args._elevated:
        print(f"[已提权运行] 管理员权限 + 完整性级别 {integrity_name(self_integrity())}\n")

    try:
        if args.list or args.list_all:
            code = cmd_list(args)
        elif args.scan:
            code = cmd_scan(args)
        elif args.doctor:
            code = cmd_doctor(args)
        elif args.shot:
            code = cmd_shot(args)
        elif (not argv and args.hwnd is None and not args.title and not args.process
              and not (args.pos or args.center or args.screen_pos)):
            code = interactive_guide()      # 双击/无参数 → 给引导
        else:
            code = cmd_click(args)
    except AppError as e:
        print(str(e), file=sys.stderr)
        code = e.code
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，已停止。", file=sys.stderr)
        code = 0
    except Exception as e:  # 兜底：别把栈糊到用户脸上
        print(f"出错了：{type(e).__name__}: {e}", file=sys.stderr)
        code = 2

    if args.keep_open:
        try:
            input("\n按回车关闭……")
        except EOFError:
            pass
    return code


if __name__ == "__main__":
    sys.exit(main())