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

键盘（1.1.0 新增，也是纯消息投递，同样不抢焦点）
--------------------------------------------------
  原来只有 WM_CHAR 文本通道，只对编辑框有效；菜单快捷键、IDE、游戏、
  画布控件都是直接读 WM_KEYDOWN 的虚拟键码，对 WM_CHAR 一律当没看见。

  标准投递（send_chord / send_key_sequence / send_text_as_keys）：
    send_text_as_keys(hwnd, "Hello!")          逐字符真按键（Shift 自动补上）
    send_chord(hwnd, parse_chord("ctrl+shift+s"))
    send_key_sequence(hwnd, [parse_chord("ctrl+a"), parse_chord("ctrl+c")])

   ★ 多键同按（按住不放）用 send_keys_state —— 只发 down 或只发 up，不配对：
    vks = parse_key_list(["ctrl", "shift", "a"])
    send_keys_state(hwnd, vks, keyup=False)     # 三键同时按住
    time.sleep(1.5)                             # 想按多久按多久
    send_keys_state(hwnd, list(reversed(vks)), keyup=True)   # 倒序松开

   lParam 按 Windows 规范组装（扫描码 / 扩展键 / Alt 的 SYS 消息升级都处理了），
   Alt 组合会自动走 WM_SYSKEYDOWN/UP。详见「键盘」那一节的注释。

鼠标滑动 / 拖拽 / 滚轮（1.2.0 新增）
------------------------------------
   do_mouse(args, body)   统一入口，服务端 /mouse 和 CLI 共用
   mouse_swipe(...)       悬停滑动：只发 WM_MOUSEMOVE，wParam=0
   mouse_drag(...)        ★ 拖拽：每一步的移动消息都带 MK_LBUTTON 等按键状态位。
                          不带的话程序认为中途已经松手，拖动会断在起点 ——
                          这是滑动类需求最常见的坑，别省这一步。
   mouse_scroll(...)      滚轮：wParam 高 16 位是增量，lParam 用**屏幕坐标**
   plan_path(...)         直线插值；取整用 round（用 int 会让短距离滑动全塌到起点）

   ★ 滑动不是「发一条消息」：拖动靠连续 WM_MOUSEMOVE 累积，所以默认按 30 步
     插值走过去。步数少 = 程序判成 0 距离；步数多 = 慢，但更像人手。

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

# --- 键盘相关 ---
# 注意：键盘消息和鼠标消息一样受 UIPI 过滤（低 IL 发不进高 IL 窗口）。
# 这几个函数只在本进程里做「按键 ↔ 字符」换算，不往目标窗口发任何东西。
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT

user32.VkKeyScanW.argtypes = [ctypes.c_wchar]
user32.VkKeyScanW.restype = ctypes.c_short

user32.ToUnicodeEx.argtypes = [
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_ubyte),
    wintypes.LPWSTR, ctypes.c_int, wintypes.UINT, wintypes.HKL,
]
user32.ToUnicodeEx.restype = ctypes.c_int

# 滚轮消息：竖滚 / 横滚。wParam 高 16 位是增量，lParam 是**屏幕坐标**。
WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x020A, 0x020E

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


def send_input_chords(chords: list[list[int]], repeat: int = 1, interval: float = 0.06,
                      use_scancode: bool = True) -> dict:
    """用 SendInput 依次投递多个组合键（每个组合一批原子提交）。"""
    sent = failed = runs = 0
    last_err = 0
    for r in range(max(1, repeat)):
        for i, vks in enumerate(chords):
            rec = send_chord_input(vks, use_scancode=use_scancode)
            sent += rec["sent"]
            if not rec["ok"]:
                failed += rec["expected"] - rec["sent"]
                last_err = rec["error"]
                break
            runs += 1
            if i < len(chords) - 1:
                time.sleep(max(0.0, interval))
        if failed:
            break
        if repeat > 1:
            time.sleep(max(0.0, interval))
    return {"ok": failed == 0 and sent > 0, "sent": sent, "failed": failed,
            "runs": runs, "repeat": max(1, repeat), "method": "sendinput",
            "scancode": bool(use_scancode), "error": last_err,
            "chords": [[hex(v) for v in c] for c in chords]}


def _key_input_for(vk: int, keyup: bool = False, use_scancode: bool = True) -> INPUT:
    """造一个键盘 INPUT 事件。use_scancode 时只交扫描码（不交虚拟键码）。"""
    scan = key_scancode(vk)
    flags = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VKS else 0
    if keyup:
        flags |= KEYEVENTF_KEYUP
    if use_scancode and scan:
        return _mk_key_input(0, scan, flags | KEYEVENTF_SCANCODE)
    return _mk_key_input(vk, scan, flags)


def send_input_keys_hold(vks: list[int], hold_seconds: float = 0.2, repeat: int = 1,
                         interval: float = 0.06, use_scancode: bool = True) -> dict:
    """
    SendInput 版的「多键同按」：按下批 + 等待 + 抬起批。

    ★ 和消息投递版的区别：这里按下的是**系统层面真实按住的键**，
      期间用户按别的键也不影响；抬起批次保证倒序（先放主键再放修饰键）。
    """
    if not vks:
        raise AppError("按键列表是空的", code=2)
    if not (0 <= hold_seconds <= 3600):
        raise AppError("hold_seconds 必须在 0~3600 秒之间", code=2)

    sent = failed = runs = 0
    for _ in range(max(1, repeat)):
        down = [_key_input_for(vk, False, use_scancode) for vk in vks]
        n_down, _ = _send_inputs(down)
        sent += n_down
        if n_down != len(down):
            failed += len(down) - n_down
            break

        time.sleep(hold_seconds)

        up = [_key_input_for(vk, True, use_scancode) for vk in reversed(vks)]
        n_up, _ = _send_inputs(up)
        sent += n_up
        runs += 1
        if n_up != len(up):
            failed += len(up) - n_up
            break
        if repeat > 1:
            time.sleep(max(0.0, interval))

    return {"ok": failed == 0 and sent > 0, "sent": sent, "failed": failed,
            "runs": runs, "held_seconds": hold_seconds, "method": "sendinput",
            "scancode": bool(use_scancode), "keys": [hex(v) for v in vks]}


def send_input_text(text: str, interval: float = 0.008, use_scancode: bool = True) -> dict:
    """
    用 SendInput 逐字符输入文本。

    ★ 走的是 KEYEVENTF_SCANCODE 路径 —— 只交扫描码，不交虚拟键码。
      这就是「用扫描码」这件事在键盘上的真实含义：让只读底层键盘输入的
      程序（DirectInput / Raw Input 类）也能认账。
    当前键盘布局打不出的字符（中文等）直接跳过并计入 skipped。
    """
    sent = failed = typed = skipped = 0
    for one in text:
        sc = int(user32.VkKeyScanW(one))
        if sc < 0 or (sc & 0xFFFF) == 0xFFFF:
            skipped += 1
            continue
        state = (sc >> 8) & 0xFF
        vks: list[int] = []
        if state & 1:
            vks.append(VK_SHIFT)
        if state & 2:
            vks.append(VK_CONTROL)
        if state & 4:
            vks.append(VK_MENU)
        vks.append(sc & 0xFF)
        rec = send_chord_input(vks, use_scancode=use_scancode)
        sent += rec["sent"]
        if not rec["ok"]:
            failed += rec["expected"] - rec["sent"]
            break
        typed += 1
        time.sleep(max(0.0, interval))
    return {"ok": failed == 0 and sent > 0, "sent": sent, "failed": failed,
            "typed_chars": typed, "skipped_unmappable": skipped,
            "method": "sendinput", "scancode": bool(use_scancode)}


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


# --------------------------------------------------------------------------
# SendInput —— 走系统输入队列的底层注入（比 mouse_event 更正规、可批量、可带扫描码）
# --------------------------------------------------------------------------
#
# 为什么要有这一层（三档输入方式的真实差别）：
#
#   ① PostMessage     只把消息塞进目标窗口队列。光标不动、焦点不动，最"干净"，
#                     但**鼠标位置是全局状态**，程序一 GetCursorPos 就露馅 ——
#                     所以移动类操作大量失效（点击是事件，反而常常有效）。
#   ② mouse_event     往系统输入队列里塞事件，真动光标，所有程序都吃。
#                     但它不支持"一次提交一批"、也不能指定扫描码。
#   ③ SendInput       ★ 系统输入队列的**正规**入口：一次可以提交一整批事件，
#                     原子性好（拖拽的按下/移动/抬起不会被别的输入插队拆散），
#                     键盘还能用 KEYEVENTF_SCANCODE 只发扫描码。
#
# ★ 关于「扫描码」（我朋友的提示，也是这次调研的重点）：
#   - 扫描码是**键盘**概念。MOUSEINPUT 的字段只有 dx/dy/mouseData/dwFlags/
#     time/dwExtraInfo，压根没有扫描码字段（本机实测确认）。
#   - 键盘的扫描码为什么重要：虚拟键码(VK)会随键盘布局和修饰键状态变化，
#     只读底层输入的 DirectInput/Raw Input 程序对 VK 路径不买账；
#     扫描码不随布局变，所以 SendInput + KEYEVENTF_SCANCODE 能"像真键盘"。
#   - 所以「用扫描码实现鼠标滑动」这个说法在字面上不成立，但**方向是对的**：
#     它指的是「别发消息，走系统输入队列」。鼠标这边的对应物是 SendInput 的
#     MOUSEEVENTF_MOVE + MOUSEEVENTF_ABSOLUTE。
#
# ★ 能力边界（必须说清楚，别让人抱错期望）：
#   SendInput 注入的输入，Raw Input 程序**仍然能识别为注入** ——
#   RAWINPUTHEADER.hDevice 为 NULL，低级钩子能看到 LLMHF_INJECTED 标志。
#   想让输入在内核视角"来自真实硬件"，只有驱动级方案（虚拟 HID 设备 /
#   Interception 这类过滤驱动）。那需要装驱动、要签名、要管理员，跳出本项目范围。
#   所以本层解决的是「程序不认消息投递」，不解决「反作弊识破注入」。

INPUT_MOUSE, INPUT_KEYBOARD, INPUT_HARDWARE = 0, 1, 2

# MOUSEEVENTF 补充标志
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
# KEYEVENTF
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0001, 0x0002, 0x0008


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


def _mk_mouse_input(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = MOUSEINPUT(dx=dx, dy=dy, mouseData=data & 0xFFFFFFFF,
                        dwFlags=flags, time=0, dwExtraInfo=None)
    return inp


def _mk_key_input(vk: int, scan: int, flags: int) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=None)
    return inp


def _send_inputs(inputs: list[INPUT]) -> tuple[int, int]:
    """
    提交一批输入事件。返回 (已发送条数, 错误码)。

    ★ 一次提交一整批而不是逐条发：系统把整批当成一个原子操作处理，
      拖拽的「按下→移动→抬起」不会被用户真实的鼠标动作插进来拆散。
      这是 SendInput 相对 mouse_event 的主要优势。
    """
    n = len(inputs)
    if n == 0:
        return 0, 0
    arr = (INPUT * n)(*inputs)
    ctypes.set_last_error(0)
    sent = int(user32.SendInput(n, arr, ctypes.sizeof(INPUT)))
    err = 0 if sent == n else (ctypes.get_last_error() or 1)
    return sent, err


SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


def _to_absolute(x: int, y: int) -> tuple[int, int]:
    """
    屏幕像素 → SendInput 的绝对坐标（0..65535 归一化）。

    ★ 归一化必须基于**虚拟屏幕**（所有显示器拼起来的那个矩形），
      并且要带 MOUSEEVENTF_VIRTUALDESK，否则多显示器下第二个屏幕的坐标会算错。
      这是 SendInput 和 SetCursorPos 最大的差别：SetCursorPos 收像素，
      SendInput 收的是 65535 比例值。
    """
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = max(1, user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
    vh = max(1, user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
    ax = int(round((x - vx) * 65535.0 / max(1, vw - 1)))
    ay = int(round((y - vy) * 65535.0 / max(1, vh - 1)))
    return max(0, min(65535, ax)), max(0, min(65535, ay))


def send_input_mouse_move(sx: int, sy: int) -> tuple[int, int]:
    """把光标移到屏幕坐标 (sx, sy)，用 SendInput 的绝对移动。"""
    ax, ay = _to_absolute(sx, sy)
    return _send_inputs([_mk_mouse_input(
        MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay)])


def send_input_key(vk: int, keyup: bool = False, use_scancode: bool = True,
                   extended: Optional[bool] = None) -> tuple[int, int]:
    """
    用 SendInput 发一个键盘事件。

    ★ use_scancode=True（默认）走 KEYEVENTF_SCANCODE：只交扫描码，
      不交虚拟键码 —— 这是让「只读底层键盘输入」的程序认账的关键（见本节开头）。
      扫描码由 MapVirtualKeyW 从 VK 换算，扫不出来的（极少数）才回退到 VK。
    """
    scan = key_scancode(vk)
    if extended is None:
        extended = vk in EXTENDED_VKS
    flags = 0
    if keyup:
        flags |= KEYEVENTF_KEYUP
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if use_scancode and scan:
        flags |= KEYEVENTF_SCANCODE
        return _send_inputs([_mk_key_input(0, scan, flags)])
    return _send_inputs([_mk_key_input(vk, scan, flags)])


def send_chord_input(vks: list[int], use_scancode: bool = True) -> dict:
    """
    用 SendInput 投递一次组合键（修饰键 → 主键 → 抬起，倒序）。

    ★ 一次调用提交一整批：按下修饰键 + 主键 + 抬起全部打包，
      中间不会被别的输入插队。这是「多键同按」在底层最可靠的实现。
    """
    if not vks:
        raise AppError("组合键为空", code=2)
    inputs = [_key_input_for(vk, False, use_scancode) for vk in vks]
    # 抬起：倒序（先放主键，再放修饰键），和真人一致
    inputs += [_key_input_for(vk, True, use_scancode) for vk in reversed(vks)]

    sent, err = _send_inputs(inputs)
    return {"ok": sent == len(inputs) and sent > 0, "sent": sent, "expected": len(inputs),
            "error": err, "method": "sendinput", "scancode": bool(use_scancode),
            "chord": [hex(v) for v in vks], "events": len(inputs)}


def send_input_swipe(hwnd: int, points: list[tuple[int, int]], button: Optional[str] = None,
                     restore_cursor: bool = True, batch: bool = True) -> dict:
    """
    SendInput 版的滑动/拖拽。

    和 mouse_swipe_hardware 的区别：
      * 走 SendInput（系统正式入口）而不是 mouse_event；
      * batch=True 时**整段轨迹一次提交**，原子性最好，程序收到的移动序列
        不会被用户真实鼠标事件插队打散（拖拽类交互最怕这个）。
      * batch=False 时逐点提交，中间能 sleep，轨迹在时间上更像人手。

    仍然会真实移动光标（SendInput 的语义就是产生真实输入）。
    """
    if button is not None and button not in HW_BUTTONS:
        raise AppError(f"button 只能是 {list(HW_BUTTONS)}，收到 {button!r}", code=2)

    down_up = HW_BUTTONS.get(button) if button else None
    saved = wintypes.POINT()
    had_saved = bool(user32.GetCursorPos(ctypes.byref(saved)))

    # 屏幕坐标序列
    pts = [resolve_screen_point(hwnd, px, py) for (px, py) in points]

    inputs: list[INPUT] = []
    if down_up:
        inputs.append(_mk_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
                                      | MOUSEEVENTF_VIRTUALDESK, *_to_absolute(*pts[0])))
        inputs.append(_mk_mouse_input(down_up[0]))
        for i, (sx, sy) in enumerate(pts[1:], 1):
            inputs.append(_mk_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
                                          | MOUSEEVENTF_VIRTUALDESK, *_to_absolute(sx, sy)))
        inputs.append(_mk_mouse_input(down_up[1]))
    else:
        for sx, sy in pts:
            inputs.append(_mk_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
                                          | MOUSEEVENTF_VIRTUALDESK, *_to_absolute(sx, sy)))

    if batch:
        sent, err = _send_inputs(inputs)
    else:
        sent = 0
        err = 0
        for one in inputs:
            s, e = _send_inputs([one])
            sent += s
            if not s:
                err = e
                break
            time.sleep(0.008)

    restored = False
    if restore_cursor and had_saved:
        time.sleep(0.03)
        restored = bool(user32.SetCursorPos(saved.x, saved.y))

    return {"ok": sent == len(inputs), "mode": "sendinput-drag" if button else "sendinput-swipe",
            "method": "sendinput", "button": button, "batched": bool(batch),
            "from": list(points[0]), "to": list(points[-1]),
            "steps": len(points) - 1, "sent": sent, "expected": len(inputs),
            "error": err, "cursor_restored": restored}


def send_input_scroll(hwnd: int, x: int, y: int, amount: int, axis: str = "vertical",
                      restore_cursor: bool = True) -> dict:
    """
    SendInput 版滚轮。先把光标移过去（滚轮事件发给光标下的窗口），再批量滚。
    mouseData 用有符号值：向下/向右为负（和 mouse_event 一样）。
    """
    MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
    flag = MOUSEEVENTF_WHEEL if axis == "vertical" else MOUSEEVENTF_HWHEEL
    saved = wintypes.POINT()
    had_saved = bool(user32.GetCursorPos(ctypes.byref(saved)))
    sx, sy = resolve_screen_point(hwnd, x, y)

    inputs = [_mk_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
                              | MOUSEEVENTF_VIRTUALDESK, *_to_absolute(sx, sy))]
    for _ in range(abs(amount)):
        delta = WHEEL_DELTA if amount > 0 else -WHEEL_DELTA
        if axis == "horizontal":
            delta = -delta
        inputs.append(_mk_mouse_input(flag, 0, 0, delta & 0xFFFFFFFF))
    sent, err = _send_inputs(inputs)

    restored = False
    if restore_cursor and had_saved:
        time.sleep(0.03)
        restored = bool(user32.SetCursorPos(saved.x, saved.y))

    return {"ok": sent == len(inputs), "mode": "sendinput-scroll", "method": "sendinput",
            "axis": axis, "amount": amount, "sent": sent, "expected": len(inputs),
            "error": err, "screen_point": [sx, sy], "cursor_restored": restored}


# --------------------------------------------------------------------------
# 鼠标滑动 / 拖拽 / 滚轮（同样是消息投递，不抢光标）
# --------------------------------------------------------------------------
#
# 为什么滑动不是「发一条消息」就完事：
#   拖动类交互（滑块、列表内容、地图、画布）都是靠**连续的 WM_MOUSEMOVE**
#   累积出来的。中间不补点，程序看到的就是「起点按下 → 瞬移到终点 → 抬起」，
#   要么当没发生，要么判成 0 距离。所以必须按步数插值，一步步走过去。
#
# ★ 拖动期间每条移动消息的 wParam 必须带 MK_LBUTTON（按键状态位）。
#   不带的话程序认为「按键已经松了」，拖动直接断在原地 —— 这是最常见的坑。
#   同理，拖右键要带 MK_RBUTTON。
#
# ★ 坐标是**客户区坐标**，插值时按客户区线性走。
#   如果目标内部还有自己的滚动偏移（画布类控件），程序会自己换算，
#   我们只管把客户区坐标走对。
#
# ★ 同样受 UIPI 约束：低完整性级别发不进高完整性级别的窗口。
#   消息被拒（错误码 5）时立即停手，不会把剩下的步数白跑一遍。

MK_SHIFT, MK_CONTROL = 0x0004, 0x0008
WHEEL_DELTA = 120


def _clamp_int(value, lo: int, hi: int, name: str, default: int) -> int:
    """整数范围校验。服务端有自己的 clamp_int，这里是库内的等价物，避免跨文件依赖。"""
    if value is None:
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise AppError(f"{name} 必须是整数，收到 {value!r}", code=2)
    if n < lo or n > hi:
        raise AppError(f"{name} 超出允许范围 [{lo}, {hi}]，收到 {n}", code=2)
    return n


def _mouse_move_msg(hwnd: int, x: int, y: int, mk: int, method: str) -> tuple[bool, int]:
    """投递一条带按键状态的 WM_MOUSEMOVE。"""
    lp = make_lparam(x, y)
    if method == "send":
        out = ctypes.c_size_t(0)
        ctypes.set_last_error(0)
        res = user32.SendMessageTimeoutW(hwnd, WM_MOUSEMOVE, mk, lp,
                                         SMTO_ABORTIFHUNG | SMTO_BLOCK, 1000,
                                         ctypes.byref(out))
        err = ctypes.get_last_error()
        if res == 0 and err:
            return False, err
        return True, 0
    return post_checked(hwnd, WM_MOUSEMOVE, mk, lp)


def plan_path(x0: int, y0: int, x1: int, y1: int, steps: int,
              ease: bool = False) -> list[tuple[int, int]]:
    """
    把一段直线拆成 steps 个点（不含起点，含终点）。

    steps=0 时返回空列表，调用方自己补一条到终点的移动。
    ease=True 用 smoothstep 平滑速度（起步慢、中间快、收尾慢），
    比匀速更像人手 —— 有些程序就是这么判断「像不像真人操作」的。

    ★ 取整用 round 而不是 int：int 会一路向下取整，短距离滑动
      （比如 10 像素分 20 步）会全部塌到起点，滑动等于没发生。
    """
    if steps <= 0:
        return []
    pts: list[tuple[int, int]] = []
    for i in range(1, steps + 1):
        t = i / steps
        if ease:
            t = t * t * (3.0 - 2.0 * t)      # smoothstep
        px = round(x0 + (x1 - x0) * t)
        py = round(y0 + (y1 - y0) * t)
        if pts and pts[-1] == (px, py):
            continue                          # 去重：重复点对程序没有意义
        pts.append((px, py))
    if not pts or pts[-1] != (x1, y1):
        pts.append((x1, y1))                  # 终点必须精确落在目标上
    return pts


def _path_from_spec(hwnd: int, start: tuple[int, int], body: dict) -> tuple[list[tuple[int, int]], dict]:
    """
    把请求里的路径写法解析成点序列。支持四种：

      dx/dy        相对滑动（最常用）：dx=0, dy=-500 → 向上滑 500 像素
      to           滑到绝对客户区坐标：to=[400, 300]
      path         一串途经点：[[100,100],[300,300],[500,200]]
      pattern      手势名：line / up / down / left / right / circle / square / zigzag
                   （up/down 这类就是「方向 + 一段固定距离」的语法糖，也可以再配 distance）

    返回 (点序列, 回显用的元信息)。
    """
    cw, ch = window_dimensions(hwnd, client_only=True)
    x0, y0 = start

    def clamp(p: tuple[int, int]) -> tuple[int, int]:
        return max(0, min(int(p[0]), cw - 1)), max(0, min(int(p[1]), ch - 1))

    distance = int(body.get("distance", 300))
    if not (1 <= distance <= 20000):
        raise AppError("distance 必须在 1~20000 像素之间", code=2)

    if body.get("path") is not None:
        raw = body["path"]
        if not isinstance(raw, list) or not raw:
            raise AppError("path 必须是坐标数组，如 [[100,100],[300,300]]", code=2)
        if len(raw) > 128:
            raise AppError("path 最多 128 个点", code=2)
        pts = []
        for item in raw:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise AppError(f"path 里的点必须是 [x, y]，收到 {item!r}", code=2)
            try:
                pts.append(clamp((int(item[0]), int(item[1]))))
            except (TypeError, ValueError):
                raise AppError(f"path 里的坐标必须是整数，收到 {item!r}", code=2)
        return pts, {"mode": "path", "points": len(pts)}

    if body.get("to") is not None:
        to = body["to"]
        if not isinstance(to, (list, tuple)) or len(to) != 2:
            raise AppError("to 必须是 [x, y]", code=2)
        try:
            dest = clamp((int(to[0]), int(to[1])))
        except (TypeError, ValueError):
            raise AppError(f"to 的坐标必须是整数，收到 {to!r}", code=2)
        steps = _clamp_int(body.get("steps"), 1, 5000, "steps", 30)
        return plan_path(x0, y0, dest[0], dest[1], steps,
                         ease=bool(body.get("ease"))), {"mode": "to", "to": list(dest)}

    pattern = body.get("pattern")
    if pattern:
        name = str(pattern).strip().lower()
        cx, cy = x0, y0
        pts: list[tuple[int, int]] = []
        if name in ("line", "up", "down", "left", "right"):
            vec = {"up": (0, -distance), "down": (0, distance),
                   "left": (-distance, 0), "right": (distance, 0),
                   "line": (0, -distance)}[name]
            if name == "line":      # line 用 dx/dy；没给就默认向上
                vec = (int(body.get("dx", 0)), int(body.get("dy", -distance)))
            steps = _clamp_int(body.get("steps"), 1, 5000, "steps", 30)
            dest = clamp((cx + vec[0], cy + vec[1]))
            pts = plan_path(cx, cy, dest[0], dest[1], steps, ease=bool(body.get("ease")))
        elif name in ("circle", "square", "zigzag"):
            points = _clamp_int(body.get("points"), 3, 720, "points",
                                72 if name == "circle" else (4 if name == "square" else 8))
            r = max(4, distance // 2)
            if name == "circle":
                import math
                for i in range(1, points + 1):
                    a = 2 * math.pi * i / points
                    pts.append(clamp((cx + r * math.cos(a), cy + r * math.sin(a))))
            elif name == "square":
                d = distance
                corners = [(cx + d, cy), (cx + d, cy + d), (cx, cy + d), (cx, cy)]
                for i in range(1, points + 1):
                    t = i / points * 4
                    seg = min(int(t), 3)
                    f = t - seg
                    a, b = corners[seg], corners[(seg + 1) % 4]
                    pts.append(clamp((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)))
            else:  # zigzag：左右来回扫，像刷列表
                seg = max(2, points // 2)
                for i in range(1, points + 1):
                    t = i / points
                    px = cx + (distance if i % 2 else -distance) * t
                    py = cy - distance * t
                    pts.append(clamp((px, py)))
        else:
            raise AppError(
                f"不认识的 pattern {pattern!r}。可用：line / up / down / left / right / "
                f"circle / square / zigzag", code=2)
        # 手势类默认回到起点：圆/方/锯齿在拖动场景下会把内容拖偏，
        # 走完一圈回到原处更安全（to_start=false 可以关掉）
        if name in ("circle", "square", "zigzag") and body.get("to_start", True):
            pts.append((x0, y0))
        return pts, {"mode": "pattern", "pattern": name, "points": len(pts)}

    # 默认：相对滑动
    dx = body.get("dx")
    dy = body.get("dy")
    if dx is None and dy is None:
        raise AppError("得给一种滑动：dx/dy、to、path 或 pattern", code=2)
    try:
        dx = int(dx or 0)
        dy = int(dy or 0)
    except (TypeError, ValueError):
        raise AppError("dx / dy 必须是整数", code=2)
    if dx == 0 and dy == 0:
        raise AppError("dx 和 dy 都是 0，滑动没有意义", code=2)
    steps = _clamp_int(body.get("steps"), 1, 5000, "steps", 30)
    dest = clamp((x0 + dx, y0 + dy))
    if dest != (x0 + dx, y0 + dy):
        dx, dy = dest[0] - x0, dest[1] - y0
    return plan_path(x0, y0, dest[0], dest[1], steps,
                     ease=bool(body.get("ease"))), {"mode": "delta", "dx": dx, "dy": dy}


def mouse_scroll(hwnd: int, x: int, y: int, amount: int, axis: str = "vertical",
                 method: str = "post", delay: float = 0.03) -> dict:
    """
    滚轮。amount 单位是「格」，正数 = 向上 / 向左，负数 = 向下 / 向右。

    竖滚发 WM_MOUSEWHEEL，横滚发 WM_MOUSEHWHEEL —— 横滚不是所有控件都支持，
    不支持的就完全没反应（不会报错，别以为是代码坏了）。

    ★ 滚轮的 wParam 里**高 16 位**是增量、低 16 位是按键状态，
      和别的鼠标消息正好相反，很容易写反。lParam 用的是**屏幕坐标**，
      不是客户区坐标（这也是滚轮消息和点击消息的区别之一）。
    """
    if axis not in ("vertical", "horizontal"):
        raise AppError("axis 只能是 vertical / horizontal", code=2)
    if not isinstance(amount, int) or amount == 0:
        raise AppError("amount 必须是非 0 整数（正数向上/向左，负数向下/向右）", code=2)
    if abs(amount) > 1000:
        raise AppError("amount 绝对值上限 1000", code=2)

    msg = 0x020A if axis == "vertical" else 0x020E
    sx, sy = resolve_screen_point(hwnd, x, y)
    lp = make_lparam(sx, sy)              # ★ 屏幕坐标
    events: list[dict] = []
    sent = failed = 0

    for _ in range(abs(amount)):
        delta = WHEEL_DELTA if amount > 0 else -WHEEL_DELTA
        if axis == "horizontal":
            delta = -delta                # 横滚方向相反：正 = 向左
        wp = ((delta & 0xFFFF) << 16) | 0  # 高 16 位增量，低 16 位按键状态
        ok, err = _dispatch_key_msg(hwnd, msg, wp, lp, method)
        events.append({"msg": hex(msg), "delta": delta, "ok": ok, "error": err})
        if ok:
            sent += 1
        else:
            failed += 1
            break
        time.sleep(max(0.0, delay))

    return {"ok": failed == 0 and sent > 0, "mode": "scroll", "axis": axis,
            "amount": amount, "sent": sent, "failed": failed,
            "screen_point": [sx, sy], "events": events[-20:]}


def mouse_drag(hwnd: int, points: list[tuple[int, int]], button: str = "left",
               method: str = "post", hold: float = 0.03, delay: float = 0.015,
               release: bool = True, deep: bool = True) -> dict:
    """
    按住一个键沿 points 走完全程。points[0] 是起点。

    ★ 拖动期间每条 WM_MOUSEMOVE 都带 MK_xxx（按键状态位）——
      不带就等于中途松手，拖动会断在起点，这是最经典的坑。

    投递顺序：
      移动到起点 → 按下 → 逐点移动（带 MK）→ 抬起
    deep=True 时和点击一样会逐层钻到最深的子控件。
    """
    if len(points) < 2:
        raise AppError("拖动至少要有起点和终点两个点", code=2)
    if button not in BUTTONS:
        raise AppError(f"button 只能是 {list(BUTTONS)}，收到 {button!r}", code=2)
    if method not in ("post", "send"):
        raise AppError("拖动只支持 post / send（hardware 真拖拽请用 bgclick.py CLI）", code=2)

    down, up, mk = BUTTONS[button]
    events: list[dict] = []
    sent = failed = 0
    # 先决定发给谁：默认钻到最深的子控件（标准控件都是子窗口，发给顶层等于石沉大海）。
    # ★ 只钻一次，起点定了就不再改 —— 拖动途中重新钻取会换成别的控件，把拖拽打断。
    send_to = hwnd
    if deep:
        send_to, _, _, _ = deepest_child_at(hwnd, points[0][0], points[0][1])

    def emit(msg: int, wp: int, lp: int, tag: str) -> bool:
        nonlocal sent, failed
        ok, err = _dispatch_key_msg(send_to, msg, wp, lp, method)
        events.append({"msg": hex(msg), "wparam": hex(wp), "lparam": hex(lp),
                       "ok": ok, "error": err, "tag": tag, "target": hex(send_to)})
        if ok:
            sent += 1
            return True
        failed += 1
        return False

    if not emit(WM_MOUSEMOVE, 0, make_lparam(*points[0]), "move-to-start"):
        return {"ok": False, "mode": "drag", "error": events[-1]["error"],
                "sent": sent, "failed": failed, "events": events[-20:]}
    time.sleep(max(0.0, hold))
    if not emit(down, mk, make_lparam(*points[0]), "down"):
        return {"ok": False, "mode": "drag", "error": events[-1]["error"],
                "sent": sent, "failed": failed, "events": events[-20:]}

    time.sleep(max(0.0, hold))
    for i, (px, py) in enumerate(points[1:], 1):
        # 每步都带到**同一个**目标窗口的客户区坐标（不重新钻取，
        # 拖动途中钻取会换来换去，反而把拖拽打断）
        if not emit(WM_MOUSEMOVE, mk, make_lparam(px, py), f"move-{i}"):
            break
        time.sleep(max(0.0, delay))

    time.sleep(max(0.0, hold))
    if release:
        last = points[-1]
        emit(up, 0, make_lparam(*last), "up")

    return {"ok": failed == 0 and sent > 1, "mode": "drag", "button": button,
            "from": list(points[0]), "to": list(points[-1]),
            "steps": len(points) - 1, "release": release,
            "sent": sent, "failed": failed, "events": events[-20:]}


def do_mouse(args, body: dict) -> dict:
    """
    鼠标滑动/拖拽/滚轮的统一入口（服务端和 CLI 共用）。

    body 里可以带：
      from / start        起点客户区坐标（默认客户区中心）
      dx / dy             相对滑动
      to                  绝对终点
      path                途经点
      pattern             手势
      steps               插值步数（默认 30）
      ease                平滑加减速
      duration            总时长秒 —— 给了就按它反推每步间隔（优先级高于 delay）
      delay               每步间隔秒（默认 0.02）
      button              拖拽时按哪个键（默认 left）；给了 button/hold 就走拖拽
      hold                按下与抬起前的额外停顿（秒）
      release             结束时是否抬起（默认 true）
      scroll              滚轮格数（正数向上，负数向下）
      axis                vertical / horizontal
      method              post / send / hardware / **sendinput**

    ★ 三种「动真格」的层次，按需要选：
      post/send   只发消息，光标不动 —— 最干净，但程序查 GetCursorPos 就失效
      hardware    SetCursorPos + mouse_event —— 真动光标，逐条发
      sendinput   ★ SendInput，系统输入队列的正规入口，可**整批原子提交**
                  （拖拽的按下/移动/抬起不会被用户真实鼠标插队拆散），
                  键盘侧还能只发扫描码。反作弊仍能识破注入（hDevice=NULL），
                  要「像真实硬件」只能上驱动级，不在本项目范围。
    """
    method = body.get("method", "post")
    if method not in ("post", "send", "hardware", "sendinput"):
        raise AppError("method 只能是 post / send / hardware / sendinput", code=2)

    hwnd = args.hwnd_int
    cw, ch = window_dimensions(hwnd, client_only=True)
    start_raw = body.get("from", body.get("start"))
    if start_raw is None:
        start = (cw // 2, ch // 2)
    else:
        if not isinstance(start_raw, (list, tuple)) or len(start_raw) != 2:
            raise AppError("from 必须是 [x, y]", code=2)
        try:
            start = (int(start_raw[0]), int(start_raw[1]))
        except (TypeError, ValueError):
            raise AppError(f"from 的坐标必须是整数，收到 {start_raw!r}", code=2)
    if not (0 <= start[0] < cw and 0 <= start[1] < ch):
        raise AppError(f"起点 {start} 落在客户区 {cw}x{ch} 之外", code=2)

    delay = float(body.get("delay", 0.02))
    if not (0 <= delay <= 60):
        raise AppError("delay 必须在 0~60 秒之间", code=2)

    # --- 滚轮 ---
    if body.get("scroll") is not None:
        axis = str(body.get("axis", "vertical")).lower()
        try:
            amount = int(body["scroll"])
        except (TypeError, ValueError):
            raise AppError("scroll 必须是整数", code=2)
        if method == "hardware":
            rec = mouse_scroll_hardware(hwnd, start[0], start[1], amount, axis=axis,
                                        restore_cursor=bool(body.get("restore_cursor", True)))
        elif method == "sendinput":
            rec = send_input_scroll(hwnd, start[0], start[1], amount, axis=axis,
                                    restore_cursor=bool(body.get("restore_cursor", True)))
        else:
            rec = mouse_scroll(hwnd, start[0], start[1], amount, axis=axis,
                               method=method, delay=max(delay, 0.01))
        rec.update({"from": list(start), "hwnd": hex(hwnd)})
        return rec

    # --- 滑动路径 ---
    pts, meta = _path_from_spec(hwnd, start, body)
    if not pts:
        raise AppError("路径是空的，没什么可滑", code=2)

    duration = body.get("duration")
    if duration is not None:
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            raise AppError("duration 必须是数字（秒）", code=2)
        if not (0 <= duration <= 3600):
            raise AppError("duration 必须在 0~3600 秒之间", code=2)
        delay = duration / max(1, len(pts))

    hold = float(body.get("hold", 0.03))
    if not (0 <= hold <= 60):
        raise AppError("hold 必须在 0~60 秒之间", code=2)

    button = body.get("button")
    is_drag = bool(button) or bool(body.get("drag"))

    if method == "sendinput":
        # SendInput：整批原子提交（拖拽最怕被用户真实鼠标插队拆散）
        rec = send_input_swipe(hwnd, [start] + pts,
                               button=(button or "left") if is_drag else None,
                               restore_cursor=bool(body.get("restore_cursor", True)),
                               batch=bool(body.get("batch", True)))
        rec["mode"] = "sendinput-drag" if is_drag else "sendinput-swipe"
    elif method == "hardware":
        # 真输入：真移动光标，所有程序都吃（代价是占用真实光标）
        rec = mouse_swipe_hardware(hwnd, [start] + pts,
                                   button=(button or "left") if is_drag else None,
                                   restore_cursor=bool(body.get("restore_cursor", True)),
                                   step_delay=delay)
        rec["mode"] = "hardware-drag" if is_drag else "hardware-swipe"
    elif is_drag:
        rec = mouse_drag(hwnd, [start] + pts, button=button or "left", method=method,
                         hold=hold, delay=delay, release=bool(body.get("release", True)),
                         deep=not bool(body.get("no_deep")))
        rec["mode"] = "drag"
    else:
        rec = mouse_swipe(hwnd, pts, method=method, delay=delay,
                          deep=not bool(body.get("no_deep")))

    input_mode = meta.pop("mode", None)
    rec.update(meta)
    rec["input_mode"] = input_mode       # delta / to / path / pattern
    rec["from"] = list(start)
    rec["to"] = list(pts[-1])
    rec["steps"] = len(pts)
    rec["ease"] = bool(body.get("ease"))
    rec["hwnd"] = hex(hwnd)
    return rec


def mouse_swipe_hardware(hwnd: int, points: list[tuple[int, int]], button: Optional[str] = None,
                         restore_cursor: bool = True, step_delay: float = 0.012) -> dict:
    """
    ★ 真输入版本：SetCursorPos + mouse_event 走真实的鼠标事件队列。

    为什么必须要有这个：
      合成的 WM_MOUSEMOVE 对**大量程序无效**，而且不是 bug、是设计 ——
      Windows 里鼠标位置是**全局状态**，程序随时可以调 GetCursorPos 查真实位置。
      很多现代程序（浏览器/Electron、游戏、自绘 UI）收到 WM_MOUSEMOVE 之后
      会去核对真实光标位置，发现没动就当这条消息不存在。
      点击为什么常常还行？因为「按下」是一个**事件**，程序认这条消息；
      而「移动」是**状态**，状态没法用消息伪造。

      所以：滑动/拖拽在合成消息无效的程序里，只有真输入这一条路。

    代价（必须说清楚）：
      * 会**真的移动用户的物理光标**，属于前台行为，会打扰正在操作的人；
      * 目标是「光标底下那个窗口」，不一定是原来那个 hwnd —— 所以调用方
        应该先把目标窗口调到前台（restore=False 时不自动做）。
      * restore_cursor=True 时结束时把光标放回原处（仅终点，不是全程）。

    button=None  → 纯移动（悬停滑动）
    button="left"/"right"/"middle" → 按下并保持，走完全程再抬起（真拖拽）
    """
    if button is not None and button not in HW_BUTTONS:
        raise AppError(f"真输入拖拽的 button 只能是 {list(HW_BUTTONS)}，收到 {button!r}", code=2)

    cw, ch = window_dimensions(hwnd, client_only=True)
    saved = wintypes.POINT()
    had_saved = bool(user32.GetCursorPos(ctypes.byref(saved)))

    events: list[dict] = []
    moved = 0
    failed = 0

    down_up = HW_BUTTONS.get(button) if button else None

    # 先落到起点，再按（按住拖拽时顺序不能反）
    first_sx, first_sy = resolve_screen_point(hwnd, points[0][0], points[0][1])
    if not user32.SetCursorPos(first_sx, first_sy):
        return {"ok": False, "mode": "hardware-drag" if button else "hardware-swipe",
                "error": ctypes.get_last_error() or ERROR_ACCESS_DENIED,
                "detail": f"SetCursorPos 到 ({first_sx},{first_sy}) 失败，取消以免误操作",
                "sent": 0, "failed": 1, "events": []}
    events.append({"action": "move", "screen_point": [first_sx, first_sy]})
    time.sleep(step_delay)

    if down_up:
        user32.mouse_event(down_up[0], 0, 0, 0, None)
        events.append({"action": "down", "button": button})
        time.sleep(0.03)

    for (px, py) in points[1:]:
        sx, sy = resolve_screen_point(hwnd, px, py)
        if not user32.SetCursorPos(sx, sy):
            failed += 1
            break
        # 真拖拽时移动消息由系统自己产生，带正确的按键状态位，不用我们伪造
        events.append({"action": "move", "point": [px, py], "screen_point": [sx, sy]})
        moved += 1
        time.sleep(step_delay)

    if down_up:
        time.sleep(0.03)
        user32.mouse_event(down_up[1], 0, 0, 0, None)
        events.append({"action": "up", "button": button})

    restored = False
    if restore_cursor and had_saved:
        time.sleep(0.03)
        restored = bool(user32.SetCursorPos(saved.x, saved.y))

    return {"ok": failed == 0 and (moved > 0 or len(points) == 1),
            "mode": "hardware-drag" if button else "hardware-swipe",
            "button": button, "from": list(points[0]), "to": list(points[-1]),
            "steps": len(points) - 1, "moved": moved, "failed": failed,
            "cursor_restored": restored, "events": events[-20:]}


def mouse_scroll_hardware(hwnd: int, x: int, y: int, amount: int, axis: str = "vertical",
                          restore_cursor: bool = True) -> dict:
    """
    ★ 真滚轮：把光标挪到目标位置上再发 mouse_event 的 WHEEL 事件。

    为什么要挪光标：系统的滚轮事件是发给**光标底下那个窗口**的，
      不挪过去就会滚错窗口 —— 这是真滚轮和 /mouse 消息投递最大的区别。

    axis="horizontal" 用 MOUSEEVENTF_HWHEEL（也不是所有程序支持横滚）。
    """
    MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
    flag = MOUSEEVENTF_WHEEL if axis == "vertical" else MOUSEEVENTF_HWHEEL

    saved = wintypes.POINT()
    had_saved = bool(user32.GetCursorPos(ctypes.byref(saved)))
    sx, sy = resolve_screen_point(hwnd, x, y)
    if not user32.SetCursorPos(sx, sy):
        return {"ok": False, "mode": "hardware-scroll",
                "error": ctypes.get_last_error() or ERROR_ACCESS_DENIED,
                "detail": f"SetCursorPos 到 ({sx},{sy}) 失败", "sent": 0, "failed": 1}
    time.sleep(0.05)

    sent = failed = 0
    for _ in range(abs(amount)):
        delta = WHEEL_DELTA if amount > 0 else -WHEEL_DELTA
        if axis == "horizontal":
            delta = -delta
        # mouse_event 的 dwData 是**有符号**的，负数要当 32 位传
        user32.mouse_event(flag, 0, 0, ctypes.c_ulong(delta & 0xFFFFFFFF).value, None)
        sent += 1
        time.sleep(0.03)

    restored = False
    if restore_cursor and had_saved:
        restored = bool(user32.SetCursorPos(saved.x, saved.y))

    return {"ok": failed == 0 and sent > 0, "mode": "hardware-scroll", "axis": axis,
            "amount": amount, "sent": sent, "failed": failed,
            "screen_point": [sx, sy], "cursor_restored": restored}


def mouse_swipe(hwnd: int, points: list[tuple[int, int]], method: str = "post",
                delay: float = 0.02, deep: bool = True) -> dict:
    """
    不按键的移动：鼠标从当前位置一路「划过」points（悬停滑动）。
    可以触发 hover 高亮、tooltip、画布上的 hover 手势。

    ★ 悬停滑动只发 WM_MOUSEMOVE，wParam=0（没有按键按下）。
      如果目标只认真的光标位置（比如游戏、DirectX），这条通路无效，
      那属于「合成消息天生无效」的情况，得用真移动。
    """
    events: list[dict] = []
    sent = failed = 0
    dest = hwnd
    if deep and points:
        dest, _, _, _ = deepest_child_at(hwnd, points[0][0], points[0][1])

    for i, (px, py) in enumerate(points):
        lp = make_lparam(px, py)
        if method == "send":
            out = ctypes.c_size_t(0)
            ctypes.set_last_error(0)
            res = user32.SendMessageTimeoutW(dest, WM_MOUSEMOVE, 0, lp,
                                             SMTO_ABORTIFHUNG | SMTO_BLOCK, 1000,
                                             ctypes.byref(out))
            err = ctypes.get_last_error()
            ok, err = (not (res == 0 and err)), (err if res == 0 else 0)
        else:
            ok, err = post_checked(dest, WM_MOUSEMOVE, 0, lp)
        events.append({"msg": hex(WM_MOUSEMOVE), "point": [px, py],
                       "wparam": "0x0", "lparam": hex(lp), "ok": ok, "error": err})
        if ok:
            sent += 1
        else:
            failed += 1
            break
        if i < len(points) - 1:
            time.sleep(max(0.0, delay))

    return {"ok": failed == 0 and sent > 0, "mode": "swipe", "sent": sent,
            "failed": failed, "events": events[-20:]}


# --------------------------------------------------------------------------
# 键盘：虚拟键 / 组合键 / 字符（同样走消息投递，不抢焦点）
# --------------------------------------------------------------------------
#
# 为什么不能只用 WM_CHAR：
#   原来的文本输入是逐字符投 WM_CHAR，只对「真的要字符」的控件（编辑框、
#   输入框）有效。菜单快捷键、IDE、游戏、画布类控件都是直接读 WM_KEYDOWN
#   的虚拟键码，收到 WM_CHAR 一律当没看见。所以必须有真正的键盘通路。
#
# 组合键（Ctrl+S、Shift+A）必须按真实顺序发：
#   修饰键按下 → 主键按下 → 主键抬起 → 修饰键抬起（倒序）
# ★ 顺序错了程序就不认：很多程序只在「主键按下那一刻修饰键仍处于按下状态」
#   时才当快捷键处理。
#
# ★ lParam 不能随手填 0：
#   bit 0-15  重复次数
#   bit 16-23 扫描码（不填的话，方向键/Delete 之类可能被认成小键盘数字）
#   bit 24    扩展键标志（右 Ctrl/Alt、方向键、Home/End/Delete、Win 键…）
#   bit 29    context code（Alt 按下期间为 1，与 WM_SYSKEYxxx 配套）
#   bit 30/31 前一次按键状态 / 转换状态（抬起时都要置 1）
#
# Alt 的坑：Alt 按下期间，Windows 把键盘消息**升级成 SYS 版本**
#   （WM_SYSKEYDOWN / WM_SYSKEYUP），不是 WM_KEYDOWN。所以这里要跟踪 Alt 状态。

WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_CHAR = 0x0102
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105

MAPVK_VK_TO_VSC = 0

# --- 虚拟键码 ---
VK_BACK, VK_TAB, VK_RETURN = 0x08, 0x09, 0x0D
VK_SHIFT, VK_CONTROL, VK_MENU, VK_CAPITAL = 0x10, 0x11, 0x12, 0x14
VK_ESCAPE, VK_SPACE = 0x1B, 0x20
VK_PRIOR, VK_NEXT, VK_END, VK_HOME = 0x21, 0x22, 0x23, 0x24
VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN = 0x25, 0x26, 0x27, 0x28
VK_SNAPSHOT, VK_INSERT, VK_DELETE = 0x2C, 0x2D, 0x2E
VK_LWIN, VK_RWIN, VK_APPS = 0x5B, 0x5C, 0x5D
VK_NUMPAD0 = 0x60
VK_MULTIPLY, VK_ADD, VK_SEPARATOR = 0x6A, 0x6B, 0x6C
VK_SUBTRACT, VK_DECIMAL, VK_DIVIDE = 0x6D, 0x6E, 0x6F
VK_NUMLOCK, VK_SCROLL = 0x90, 0x91
VK_LSHIFT, VK_RSHIFT = 0xA0, 0xA1
VK_LCONTROL, VK_RCONTROL = 0xA2, 0xA3
VK_LMENU, VK_RMENU = 0xA4, 0xA5
# OEM 键（美式布局的位置名，中文键盘位置一致）
VK_OEM_PLUS, VK_OEM_COMMA, VK_OEM_MINUS, VK_OEM_PERIOD = 0xBB, 0xBC, 0xBD, 0xBE
VK_OEM_2, VK_OEM_3 = 0xBF, 0xC0
VK_OEM_4, VK_OEM_5, VK_OEM_6, VK_OEM_7 = 0xDB, 0xDC, 0xDD, 0xDE

# 需要置 lParam bit24（扩展键）的键。不置的后果：程序把方向键认成小键盘数字。
EXTENDED_VKS = frozenset({
    VK_RCONTROL, VK_RMENU, VK_INSERT, VK_DELETE, VK_HOME, VK_END,
    VK_PRIOR, VK_NEXT, VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN,
    VK_NUMLOCK, VK_DIVIDE, VK_SNAPSHOT, VK_LWIN, VK_RWIN, VK_APPS,
})

_KEY_TABLE: dict[str, int] = {
    # 编辑键
    "backspace": VK_BACK, "bs": VK_BACK, "back": VK_BACK,
    "tab": VK_TAB, "enter": VK_RETURN, "return": VK_RETURN, "cr": VK_RETURN,
    "esc": VK_ESCAPE, "escape": VK_ESCAPE, "space": VK_SPACE, "spacebar": VK_SPACE,
    "insert": VK_INSERT, "ins": VK_INSERT, "delete": VK_DELETE, "del": VK_DELETE,
    "home": VK_HOME, "end": VK_END,
    "pageup": VK_PRIOR, "pgup": VK_PRIOR, "pagedown": VK_NEXT, "pgdn": VK_NEXT,
    # 方向键
    "left": VK_LEFT, "up": VK_UP, "right": VK_RIGHT, "down": VK_DOWN,
    # 修饰键
    "shift": VK_SHIFT, "lshift": VK_LSHIFT, "rshift": VK_RSHIFT,
    "ctrl": VK_CONTROL, "control": VK_CONTROL, "ctl": VK_CONTROL,
    "lctrl": VK_LCONTROL, "lcontrol": VK_LCONTROL,
    "rctrl": VK_RCONTROL, "rcontrol": VK_RCONTROL,
    "alt": VK_MENU, "menu": VK_MENU, "lalt": VK_LMENU, "ralt": VK_RMENU,
    "win": VK_LWIN, "lwin": VK_LWIN, "rwin": VK_RWIN, "super": VK_LWIN, "cmd": VK_LWIN,
    "apps": VK_APPS, "contextmenu": VK_APPS,
    # 锁定 / 系统
    "capslock": VK_CAPITAL, "caps": VK_CAPITAL,
    "numlock": VK_NUMLOCK, "scrolllock": VK_SCROLL, "scroll": VK_SCROLL,
    "printscreen": VK_SNAPSHOT, "prtsc": VK_SNAPSHOT, "pause": 0x13, "break": 0x03,
    # 小键盘符号
    "multiply": VK_MULTIPLY, "add": VK_ADD, "subtract": VK_SUBTRACT,
    "decimal": VK_DECIMAL, "divide": VK_DIVIDE, "separator": VK_SEPARATOR,
    # 多媒体
    "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
    "next_track": 0xB0, "prev_track": 0xB1, "stop_media": 0xB2, "play_pause": 0xB3,
    # OEM 符号键（'+' / '-' / '.' / ',' 这些字符本身建议写成 plus/minus/period/comma，
    # 因为 '+' 是组合键的分隔符，直接写 ctrl++ 会被解析坏）
    "plus": VK_OEM_PLUS, "equal": VK_OEM_PLUS, "equals": VK_OEM_PLUS,
    "minus": VK_OEM_MINUS, "comma": VK_OEM_COMMA, "period": VK_OEM_PERIOD,
    "dot": VK_OEM_PERIOD, "slash": VK_OEM_2, "grave": VK_OEM_3, "backtick": VK_OEM_3,
    "bracketleft": VK_OEM_4, "lbracket": VK_OEM_4,
    "backslash": VK_OEM_5, "bracketright": VK_OEM_6, "rbracket": VK_OEM_6,
    "quote": VK_OEM_7, "apostrophe": VK_OEM_7,
}
for _i in range(1, 25):                       # F1 = 0x70 ... F24 = 0x87
    _KEY_TABLE[f"f{_i}"] = 0x6F + _i
for _i in range(10):                          # 小键盘 0-9
    _KEY_TABLE[f"numpad{_i}"] = VK_NUMPAD0 + _i


def _is_alt_vk(vk: int) -> bool:
    return vk in (VK_MENU, VK_LMENU, VK_RMENU)


def _is_modifier_vk(vk: int) -> bool:
    return vk in (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LSHIFT, VK_RSHIFT,
                  VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN)


def _resolve_key_token(token: str) -> list[int]:
    """
    把一个按键名/字符解析成虚拟键序列。

    返回列表是因为一个字符可能需要配修饰键才打得出来：
    "!" → [VK_SHIFT, 0x31]，"a" → [0x41]。
    """
    t = token.strip()
    low = t.lower()
    if not t:
        raise AppError("按键名是空的", code=2)
    if low in _KEY_TABLE:
        return [_KEY_TABLE[low]]
    if len(t) == 1:
        if "a" <= low <= "z":
            return [ord(low.upper())]
        if "0" <= t <= "9":
            return [ord(t)]
        sc = int(user32.VkKeyScanW(t))
        if sc >= 0 and (sc & 0xFFFF) != 0xFFFF:
            out: list[int] = []
            state = (sc >> 8) & 0xFF
            if state & 1:
                out.append(VK_SHIFT)
            if state & 2:
                out.append(VK_CONTROL)
            if state & 4:
                out.append(VK_MENU)
            out.append(sc & 0xFF)
            return out
        raise AppError(f"当前键盘布局打不出字符 {t!r}（可以改用 --text 走字符通道）", code=2)
    if low.startswith("0x"):
        try:
            return [int(low, 16)]
        except ValueError:
            pass
    if t.isdigit():
        return [int(t)]
    raise AppError(
        f"认不出的按键名 {t!r}。可用：a-z / 0-9 / f1-f24 / enter / esc / tab / space / "
        f"backspace / delete / home / end / pageup / pagedown / up / down / left / right / "
        f"ctrl / shift / alt / win / plus / minus / comma / period …，"
        f"也可以直接写虚拟键码（0x41 或 65）", code=2)


def parse_chord(spec: str) -> list[int]:
    """
    解析组合键字符串。返回「按下顺序」的虚拟键列表，最后一个是主键。

      "ctrl+shift+s" → [VK_CONTROL, VK_SHIFT, 0x53]
      "shift+a"      → [VK_SHIFT, 0x41]
      "alt+f4"       → [VK_MENU, 0x73]
      "enter"        → [0x0D]

    ★ 分隔符只有 '+'（两侧空格随意）。想按加号键本身请写 ctrl+plus。
    """
    raw = [t.strip() for t in re.split(r"\+", spec) if t.strip()]
    if not raw:
        raise AppError("组合键是空的", code=2)

    vks: list[int] = []
    for t in raw:
        vks.extend(_resolve_key_token(t))

    # 主键 = 最后一个「非修饰键」；整串都是修饰键时就取最后一个
    main_idx = None
    for i, vk in enumerate(vks):
        if not _is_modifier_vk(vk):
            main_idx = i
    if main_idx is None:
        main_idx = len(vks) - 1

    mods = vks[:main_idx] + vks[main_idx + 1:]
    return mods + [vks[main_idx]]


def make_key_lparam(scancode: int, extended: bool = False, keyup: bool = False,
                    repeat: int = 1, alt_down: bool = False) -> int:
    """按 Windows 的键盘消息规范组装 lParam（位含义见本节开头）。"""
    lp = repeat & 0xFFFF
    lp |= (scancode & 0xFF) << 16
    if extended:
        lp |= 1 << 24
    if alt_down:
        lp |= 1 << 29
    if keyup:
        lp |= (1 << 30) | (1 << 31)
    return lp


def key_scancode(vk: int) -> int:
    """虚拟键码 → 扫描码（填进 lParam 的 bit16-23）。"""
    try:
        sc = int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC))
    except Exception:
        return 0
    return sc & 0xFF


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


def key_char(vk: int, shift: bool = False, ctrl: bool = False,
             alt: bool = False, caps: bool = False) -> Optional[str]:
    """
    算出「这个键在当前键盘布局下会打出什么字符」。

    用 ToUnicodeEx 而不是 MapVirtualKey，因为只有它认得出 Shift 状态：
    MapVirtualKey(VK_TO_CHAR) 对 'A' 永远返回 'a'，Shift+1 也算不出 '!'。
    0x4 标志是「不要改动内核里的键盘状态」，免得污染用户的真实输入。
    """
    state = (ctypes.c_ubyte * 256)()
    if shift:
        state[VK_SHIFT] = 0x80
    if ctrl:
        state[VK_CONTROL] = 0x80
    if alt:
        state[VK_MENU] = 0x80
    if caps:
        state[VK_CAPITAL] = 0x01
    buf = ctypes.create_unicode_buffer(8)
    try:
        n = int(user32.ToUnicodeEx(vk, key_scancode(vk), state, buf, 8, 0x4, None))
    except Exception:
        return None
    if n >= 1:
        s = buf[:n]
        return s if s else None
    return None                     # 0 = 打不出字符，-1 = 死键


def _stalled(events: list[dict]) -> bool:
    """一旦有消息被拒（UIPI 等），后面的就不用发了 —— 结果只会一样。"""
    return bool(events) and not events[-1].get("ok")


def send_chord(hwnd: int, vks: list[int], method: str = "post", hold: float = 0.02,
               with_char: Optional[bool] = None) -> dict:
    """
    投递**一次**组合键。vks 的最后一个是主键，前面的都是要同时按住的修饰键。

    with_char：
      True  → 主键按下后补一条 WM_CHAR（保证编辑框真的出字符）
      False → 只发按键消息
      None  → 自动：不含 Ctrl / Alt / Win 时才补（Shift+字母 会补 'A'；
              Ctrl+S 不补，免得给程序塞一个 0x13 控制字符）

    返回 {"ok", "sent", "failed", "events": [...]}
    """
    if not vks:
        raise AppError("组合键为空", code=2)

    main = vks[-1]
    mods = vks[:-1]
    shift = any(v in (VK_SHIFT, VK_LSHIFT, VK_RSHIFT) for v in vks)
    ctrl = any(v in (VK_CONTROL, VK_LCONTROL, VK_RCONTROL) for v in vks)
    alt = any(_is_alt_vk(v) for v in vks)
    win = any(v in (VK_LWIN, VK_RWIN) for v in vks)

    if with_char is None:
        with_char = not (ctrl or alt or win)

    ch = key_char(main, shift=shift, ctrl=ctrl, alt=alt) if with_char else None

    events: list[dict] = []
    sent = 0
    failed = 0
    alt_state = False

    def fire(vk: int, keyup: bool) -> None:
        nonlocal sent, failed
        if alt_state:
            msg = WM_SYSKEYUP if keyup else WM_SYSKEYDOWN
        else:
            msg = WM_KEYUP if keyup else WM_KEYDOWN
        lp = make_key_lparam(key_scancode(vk), vk in EXTENDED_VKS,
                             keyup=keyup, alt_down=alt_state)
        ok, err = _dispatch_key_msg(hwnd, msg, vk, lp, method)
        events.append({"vk": vk, "vk_hex": hex(vk), "msg": hex(msg),
                       "keyup": keyup, "ok": ok, "error": err})
        if ok:
            sent += 1
        else:
            failed += 1

    # 1) 修饰键按下（Alt 要先更新状态：之后的消息得升级成 SYS 版本）
    for vk in mods:
        if _is_alt_vk(vk):
            alt_state = True
        fire(vk, False)
        if _stalled(events):
            break

    if not _stalled(events):
        time.sleep(max(0.0, hold))
        # 2) 主键按下
        fire(main, False)
        # 3) 补 WM_CHAR（走字符通路的控件只认这个）
        if ch and not _stalled(events):
            lp = make_key_lparam(key_scancode(main), main in EXTENDED_VKS, alt_down=alt_state)
            ok, err = _dispatch_key_msg(hwnd, WM_CHAR, ord(ch[0]), lp, method)
            events.append({"vk": main, "vk_hex": hex(main), "msg": hex(WM_CHAR),
                           "char": ch[0], "keyup": False, "ok": ok, "error": err})
            if ok:
                sent += 1
            else:
                failed += 1
        # 4) 主键抬起
        if not _stalled(events):
            fire(main, True)
        time.sleep(max(0.0, hold))

    # 5) 修饰键抬起（倒序，和真实键盘一致）
    for vk in reversed(mods):
        if _stalled(events):
            break
        fire(vk, True)
        if _is_alt_vk(vk):
            alt_state = False

    return {"ok": failed == 0 and sent > 0, "sent": sent, "failed": failed,
            "chord": [hex(v) for v in vks], "with_char": bool(with_char),
            "char": ch, "events": events}


def send_key_sequence(hwnd: int, chords: list[list[int]], method: str = "post",
                      repeat: int = 1, interval: float = 0.06, hold: float = 0.02,
                      with_char: Optional[bool] = None) -> dict:
    """
    投递一串组合键（每个 chord 一次完整的按下-抬起），可重复。
    interval 是「两次组合之间」的间隔，hold 是「修饰键与主键之间」的间隔。
    """
    events: list[dict] = []
    sent = 0
    failed = 0
    runs = 0
    stop = False

    for _ in range(max(1, repeat)):
        if stop:
            break
        for i, vks in enumerate(chords):
            rec = send_chord(hwnd, vks, method=method, hold=hold, with_char=with_char)
            events.extend(rec["events"])
            sent += rec["sent"]
            failed += rec["failed"]
            runs += 1
            if rec["failed"]:
                stop = True          # 失败即停，别把同一个错误刷一屏
                break
            if i < len(chords) - 1:
                time.sleep(max(0.0, interval))
        if not stop and repeat > 1:
            time.sleep(max(0.0, interval))

    return {"ok": failed == 0 and sent > 0, "sent": sent, "failed": failed,
            "runs": runs,
            "chords": [[hex(v) for v in c] for c in chords],
            "repeat": max(1, repeat), "method": method,
            "events": events[-40:]}


def send_text_as_keys(hwnd: int, text: str, method: str = "post", hold: float = 0.015,
                      interval: float = 0.01, mode: str = "auto") -> dict:
    """
    逐字符输入文本。

    ★ 为什么要 mode（真实踩到的 bug）：
      最早这里对每个字符**同时**发 WM_KEYDOWN 和 WM_CHAR，想着「两条路都喂，
      总有一条中」。结果在记事本里打 "Hello!" 出来的是 "Hheelllloo!1" ——
      字符全翻倍。原因：
        * 我们发的 WM_CHAR 带的是正确字符（Shift 也算了，'H' 是 'H'）；
        * 而 WM_KEYDOWN 里的 Shift 是按**规范**发的（我们自己的状态机），
          真实键盘状态里并没有人按着 Shift，于是程序自己翻译出的是 'h'。
      两条通道都被同一类控件吃下 → 大写变小写，而且每个字符出两遍。
      所以「双通道」是错的，必须二选一。

    mode：
      "auto"    （默认）**全部走字符通道（WM_CHAR），每个字符只发一条消息**。
                输出精确：大小写、'!' 这类符号都按你给的原样落地。
                记事本、编辑框、输入框都吃这条 —— 也就是「打字」的默认选择。
      "keys"    全部走按键通道（WM_KEYDOWN/UP），一条 WM_CHAR 都不发。
                给那些直接读虚拟键码、不认 WM_CHAR 的程序（IDE、游戏、画布）。
                ★ 代价：目标自己翻译字符时用的是**真实键盘状态**（没人按着 Shift），
                  "Hello!" 可能落成 "hello1" —— 需要精确文本就别用这个模式。
      "both"    ★ 两条通道都发（老行为，会翻倍）。只有目标只认其中一条、
                你又懒得试的时候碰运气用；用在记事本上必然字符重复。

    当前键盘布局打不出的字符（中文、emoji 等）无论哪种模式都只能走 WM_CHAR。
    """
    if mode not in ("auto", "keys", "both"):
        raise AppError("mode 只能是 auto / keys / both", code=2)

    events: list[dict] = []
    sent = 0
    failed = 0
    typed = 0
    fallback = 0
    via_keys = 0
    via_char = 0

    for one in text:
        if failed:
            break
        sc = int(user32.VkKeyScanW(one))
        # 打不出的字符：只有 WM_CHAR 一条路
        if sc < 0 or (sc & 0xFFFF) == 0xFFFF:
            ok, err = _dispatch_key_msg(hwnd, WM_CHAR, ord(one), 1, method)
            events.append({"char": one, "msg": hex(WM_CHAR), "ok": ok,
                           "error": err, "via": "wm_char"})
            fallback += 1
            via_char += 1
            if ok:
                sent += 1
            else:
                failed += 1
            time.sleep(max(0.0, interval))
            continue

        # --- auto：精确字符通道，一条消息一个字符 ---
        if mode == "auto":
            # lParam 的 bit0-15 是重复次数，这里给 1（和真实按键一致）
            lp = make_key_lparam(0, False, False, repeat=1)
            ok, err = _dispatch_key_msg(hwnd, WM_CHAR, ord(one), lp, method)
            events.append({"char": one, "msg": hex(WM_CHAR), "ok": ok,
                           "error": err, "via": "wm_char"})
            via_char += 1
            typed += 1
            if ok:
                sent += 1
            else:
                failed += 1
            time.sleep(max(0.0, interval))
            continue

        # --- keys / both：按键通道（按 VkKeyScan 的修饰键状态发）---
        state = (sc >> 8) & 0xFF
        vks: list[int] = []
        if state & 1:
            vks.append(VK_SHIFT)
        if state & 2:
            vks.append(VK_CONTROL)
        if state & 4:
            vks.append(VK_MENU)
        vks.append(sc & 0xFF)

        rec = send_chord(hwnd, vks, method=method, hold=hold,
                         with_char=(mode == "both"))
        events.extend(rec["events"])
        sent += rec["sent"]
        failed += rec["failed"]
        typed += 1
        if mode == "both":
            via_char += 1
        else:
            via_keys += 1
        time.sleep(max(0.0, interval))

    return {"ok": failed == 0 and sent > 0, "sent": sent, "failed": failed,
            "typed_chars": typed, "wm_char_fallback": fallback,
            "mode": mode, "via_keys": via_keys, "via_char": via_char,
            "method": method, "events": events[-40:]}


def parse_key_list(items) -> list[int]:
    """
    把 ["ctrl", "shift", "a"] 按**原顺序**解析成虚拟键列表。

    和 parse_chord 的区别：这里不把主键挪到末尾 —— 给「按住 / 松开」用，
    顺序由调用方说了算。
    """
    out: list[int] = []
    for t in items:
        out.extend(_resolve_key_token(str(t)))
    if not out:
        raise AppError("按键列表是空的", code=2)
    return out


def send_keys_state(hwnd: int, vks: list[int], keyup: bool, method: str = "post",
                    include_char: bool = False) -> dict:
    """
    只发「按下」或只发「松开」，不配对 —— 用来实现真正的多键同按：
    先 down [ctrl, shift, a]，隔一会儿再 up [a, shift, ctrl]。

    include_char=True 时，在主键按下后补一条 WM_CHAR（仅 keyup=False 有意义）。
    """
    events: list[dict] = []
    sent = 0
    failed = 0
    alt_state = any(_is_alt_vk(v) for v in vks)   # 松开 Alt 时它自己仍算「Alt 按下中」

    for vk in vks:
        if not keyup and _is_alt_vk(vk):
            alt_state = True
        if alt_state:
            msg = WM_SYSKEYUP if keyup else WM_SYSKEYDOWN
        else:
            msg = WM_KEYUP if keyup else WM_KEYDOWN
        lp = make_key_lparam(key_scancode(vk), vk in EXTENDED_VKS,
                             keyup=keyup, alt_down=alt_state)
        ok, err = _dispatch_key_msg(hwnd, msg, vk, lp, method)
        events.append({"vk": vk, "vk_hex": hex(vk), "msg": hex(msg),
                       "keyup": keyup, "ok": ok, "error": err})
        if ok:
            sent += 1
        else:
            failed += 1
            break                      # 失败即停

    if include_char and not keyup and not failed:
        main = vks[-1]
        shift = any(v in (VK_SHIFT, VK_LSHIFT, VK_RSHIFT) for v in vks)
        ctrl = any(v in (VK_CONTROL, VK_LCONTROL, VK_RCONTROL) for v in vks)
        alt = any(_is_alt_vk(v) for v in vks)
        ch = None if (ctrl or alt) else key_char(main, shift=shift)
        if ch:
            lp = make_key_lparam(key_scancode(main), main in EXTENDED_VKS, alt_down=alt)
            ok, err = _dispatch_key_msg(hwnd, WM_CHAR, ord(ch[0]), lp, method)
            events.append({"vk": main, "vk_hex": hex(main), "msg": hex(WM_CHAR),
                           "char": ch[0], "keyup": False, "ok": ok, "error": err})
            if ok:
                sent += 1
            else:
                failed += 1

    return {"ok": failed == 0 and sent > 0, "sent": sent, "failed": failed,
            "keyup": keyup, "events": events}


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
    p.add_argument("--method", choices=["post", "send", "hardware", "sendinput"],
                   default="post",
                   help="投递方式（post 默认纯后台 / send 同步 / hardware 真输入 / "
                        "sendinput 系统输入队列+扫描码）")
    p.add_argument("--no-deep", dest="deep", action="store_false", default=True,
                   help="不向下钻取子窗口，直接发给顶层窗口")
    p.add_argument("--restore-cursor", dest="restore_cursor", action="store_true",
                   default=True, help="hardware 模式下结束后把光标放回原处（默认就放回）")
    p.add_argument("--no-restore-cursor", dest="restore_cursor", action="store_false",
                   help="hardware 模式下不恢复光标（留在原地）")
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

    # ---- 滑动 / 拖拽 / 滚轮（不加 --swipe 时这些参数不生效）----
    p.add_argument("--swipe", action="store_true",
                   help="滑动手势模式：用 --dx/--dy 或 --to / --path / --pattern 指定轨迹")
    p.add_argument("--from", dest="from_pos", type=parse_pair,
                   help="滑动起点客户区坐标 X,Y（默认客户区中心）")
    p.add_argument("--dx", type=int, default=0, help="水平位移（正数向右）")
    p.add_argument("--dy", type=int, default=0, help="垂直位移（正数向下，负数向上）")
    p.add_argument("--to", dest="to_pos", type=parse_pair, help="滑到绝对客户区坐标 X,Y")
    p.add_argument("--path", help='途经点，形如 "100,100;300,300;500,200"')
    p.add_argument("--pattern", choices=["line", "up", "down", "left", "right",
                                         "circle", "square", "zigzag"],
                   help="手势轨迹（圆/方/锯齿默认会绕回起点，可用 --no-to-start 关掉）")
    p.add_argument("--distance", type=int, default=300, help="手势轨迹的尺度（像素，默认 300）")
    p.add_argument("--points", type=int, default=None, help="手势采样点数（圆默认 72）")
    p.add_argument("--steps", type=int, default=30, help="插值步数（默认 30，越多越平滑）")
    p.add_argument("--duration", type=float, default=None,
                   help="整段滑动的总时长秒（给了就按它反推每步间隔）")
    p.add_argument("--delay", type=float, default=0.02, help="每步间隔秒（默认 0.02）")
    p.add_argument("--hold", type=float, default=0.03, help="按下/抬起前的停顿秒（拖拽用）")
    p.add_argument("--ease", action="store_true", help="平滑加减速（起步慢收尾慢，更像人手）")
    p.add_argument("--drag", action="store_true", help="按住并拖动（配合 --dx/--dy/--to/--path）")
    p.add_argument("--release", action="store_true", default=True, help="结束时抬起（默认就是抬）")
    p.add_argument("--no-release", dest="release", action="store_false",
                   help="结束时不抬键（留一个按住状态，慎用）")
    p.add_argument("--no-to-start", dest="to_start", action="store_false", default=True,
                   help="手势走完不回到起点")
    # 注意：--no-deep 在上面（点击那组参数里）已经注册过，这里不能重复注册，
    # 否则 argparse 直接抛 "conflicting option string" —— 滑动复用同一个开关。
    p.add_argument("--scroll", type=int, default=None,
                   help="滚轮格数（正数向上/向左，负数向下/向右）")
    p.add_argument("--axis", default="vertical", choices=["vertical", "horizontal"],
                   help="滚轮方向轴")
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
        # ★ 真输入方式（hardware / sendinput）不适用「钻子控件」：它们是真的
        #   把光标移过去点，系统自己决定命中谁，探测结果没有意义。
        real_click = args.method in ("hardware", "sendinput")
        hier = (probe_hierarchy(hwnd, x, y)
                if args.deep and not real_click else None)

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
            # 智能投递：逐层探测，选最深且可投递的窗口（真输入方式不探测，见上）
            if args.deep and not real_click:
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


def cmd_swipe(args) -> int:
    """
    CLI 入口：滑动 / 拖拽 / 滚轮。

    post / send 走消息投递，不动真实光标；
    hardware 走真输入（SetCursorPos + mouse_event），会占用真实光标但所有程序都吃；
    sendinput 走系统输入队列，整批原子提交（拖拽最稳），也会占用真实光标。
    """
    real_input = args.method in ("hardware", "sendinput")

    cands = match_windows(args)
    if not cands:
        raise AppError("没找到匹配的窗口。跑一下 --list 看看？", code=1)
    if args.index >= len(cands) or args.index < 0:
        msg = [f"匹配到 {len(cands)} 个窗口，--index {args.index} 越界"]
        msg += [f"  [{i}] {c['hwnd_hex']} {c['process']} | {c['title']}"
                for i, c in enumerate(cands)]
        raise AppError("\n".join(msg))

    target = cands[args.index]
    body: dict = {"method": args.method, "delay": args.delay, "hold": args.hold,
                  "ease": args.ease, "no_deep": not args.deep,
                  "release": args.release, "steps": args.steps,
                  "restore_cursor": bool(getattr(args, "restore_cursor", True))}
    if args.from_pos:
        body["from"] = list(args.from_pos)
    if args.duration is not None:
        body["duration"] = args.duration
    if args.scroll is not None:
        body["scroll"] = args.scroll
        body["axis"] = args.axis
    else:
        if args.pattern:
            body["pattern"] = args.pattern
            body["distance"] = args.distance
        elif args.path:
            pts = []
            for chunk in args.path.replace("，", ",").split(";"):
                chunk = chunk.strip()
                if not chunk:
                    continue
                try:
                    a, b = chunk.split(",")
                    pts.append([int(a.strip(), 0), int(b.strip(), 0)])
                except Exception:
                    raise AppError(f"--path 格式应为 \"x,y;x,y\"，收到 {chunk!r}", code=2)
            body["path"] = pts
        elif args.to_pos:
            body["to"] = list(args.to_pos)
        else:
            body["dx"], body["dy"] = args.dx, args.dy
        if args.drag:
            body["button"] = args.button
        elif args.button != "left":
            body["button"] = args.button

    if not args.json:
        kind = ("滚轮" if args.scroll is not None
                else ("拖拽" if body.get("button") else "悬停滑动"))
        print(f"本进程权限：{'管理员' if is_admin() else '普通用户'}")
        print(f"目标：{target['hwnd_hex']}  {target['process']}  「{target['title'][:40]}」")
        print(f"{kind} / 方式 {args.method} / 步数 {args.steps}"
              + ("（平滑加减速）" if args.ease else ""))
        if real_input:
            print(f"★ {args.method} 真输入：会占用你的真实光标，期间别动鼠标。"
                  "若目标被遮挡，操作的是上层窗口。")
            if not args.restore_cursor:
                print("★ --no-restore-cursor：结束时不会把光标放回去。")

    fake = argparse.Namespace(hwnd_int=target["hwnd"])
    rec = do_mouse(fake, body)
    rec.update({"hwnd": target["hwnd_hex"], "title": target["title"],
                "process": target["process"]})

    if args.json:
        print(json.dumps(rec, ensure_ascii=False))
    else:
        if rec.get("ok"):
            if rec.get("mode") == "scroll":
                print(f"已滚 {rec['amount']} 格（{rec['axis']}），"
                      f"投递 {rec['sent']} 条消息，落在客户区 {rec.get('from')}")
            elif str(rec.get("mode", "")).startswith(("hardware", "sendinput")):
                extra = "，光标已放回原处" if rec.get("cursor_restored") else ""
                if rec.get("batched") is not None:
                    extra = ("，整批原子提交" if rec.get("batched") else "，逐点提交") + extra
                print(f"真输入{'拖拽' if 'drag' in rec.get('mode', '') else '滑动'}完成："
                      f"{rec.get('from')} → {rec.get('to')}，"
                      f"{rec.get('sent')}/{rec.get('expected')} 条事件{extra}")
            else:
                print(f"{'拖拽' if rec.get('button') else '滑动'}完成："
                      f"{rec.get('from')} → {rec.get('to')}，"
                      f"共 {rec.get('steps')} 步 / 投递 {rec.get('sent')} 条消息")
        else:
            err = rec.get("error", 0)
            print(f"投递失败：{describe_error(err)}", file=sys.stderr)
            if err == ERROR_ACCESS_DENIED:
                print(">>> UIPI 拦截：目标完整性级别比本进程高。加 --elevate 重跑。",
                      file=sys.stderr)
            return 4 if err == ERROR_ACCESS_DENIED else 5
    return 0 if rec.get("ok") else 5


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
        elif args.swipe or args.scroll is not None:
            code = cmd_swipe(args)
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