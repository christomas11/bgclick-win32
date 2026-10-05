#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bgtray.py —— bgserver 的托盘控制台（纯 ctypes，零第三方依赖）

给常驻服务配一个系统托盘图标，用户能直观地：
  * 看服务状态（✓ 运行中 / 管理员 / 当前 IL）
  * 打开日志、打开截图目录、打开状态目录
  * 重启服务、停止服务
  * 复制 token（给调试用）

为什么不用 pystray / infi.systray：
  这个项目坚持零第三方依赖，打包成 exe 时才干净、体积才小。
  Shell_NotifyIcon + 一个隐藏窗口 + 消息循环，几十行 ctypes 就够。

线程模型（重要）：
  Win32 的托盘消息必须由**创建托盘图标的那个线程**处理。
  所以 tray.run() 会阻塞并跑自己的消息循环，
  必须放在主线程，或者放在独占线程里且不与其他消息循环共享。
  本项目的做法：把 HTTP 服务放在后台线程，托盘消息循环占主线程。

本文件相对最初版本的修复（1.0.1）：
  1. _show_menu 里 TrackPopupMenu 之后补 PostMessageW(WM_NULL)。
     —— MSDN 硬性要求，缺了它菜单会「粘住」或点击项无反应。
  2. _wndproc 的 WM_TRAYICON 分支不再 return 0 把消息吃掉。
  3. _open_path 不再静默吞异常：路径不存在时抛 FileNotFoundError，
     让菜单回调的 except 能拿到真实原因并弹气泡。
  4. _add_icon 检查 Shell_NotifyIconW 返回值，失败抛 OSError。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

# --- 常量 ---
WM_USER = 0x0400
WM_TRAYICON = WM_USER + 20
WM_NULL = 0x0000
WM_COMMAND = 0x0111
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0x00000000, 0x00000001, 0x00000002
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10

MF_STRING, MF_SEPARATOR, MF_CHECKED, MF_GRAYED = 0x0000, 0x0800, 0x0008, 0x0001
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100

IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x0010, 0x0040
IDI_APPLICATION = 32512

CW_USEDEFAULT = 0x80000000

# LRESULT 在 64 位下是 LONG_PTR，必须用 c_ssize_t（而不是 c_long，那是 32 位）
LRESULT = ctypes.c_ssize_t
WNDPROCTYPE = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEX(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT), ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROCTYPE), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON),
    ]


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND), ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD), ("pt", POINT),
    ]


user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = wintypes.HWND
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEX)]
user32.RegisterClassExW.restype = wintypes.ATOM
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.CreatePopupMenu.argtypes = []
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR]
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
user32.TrackPopupMenu.restype = wintypes.BOOL
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
user32.LoadIconW.restype = wintypes.HICON
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.RegisterWindowMessageW.restype = wintypes.UINT
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATA)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                  wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
shell32.ShellExecuteW.restype = wintypes.HINSTANCE


def _open_path(path: str) -> None:
    """
    用资源管理器打开文件或目录。

    ★ 修复：不再静默吞异常。路径不存在时抛 FileNotFoundError，
    让调用方（菜单回调）能拿到真实原因并弹气泡告知用户。
    之前 except: pass 会让用户看到「点了没反应」，完全无从排查。
    """
    if not path:
        raise ValueError("路径为空")
    if os.path.isdir(path):
        os.startfile(path)          # noqa: S606
    elif os.path.exists(path):
        # explorer /select, 对含空格/中文的路径用列表传参，别拼字符串
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        raise FileNotFoundError(f"路径不存在：{path}")


class TrayApp:
    """
    托盘图标 + 右键菜单。

    callbacks: 菜单项文字 -> 无参函数。返回值忽略。
    菜单里以 "-" 开头表示分隔线，以 "!" 开头表示默认禁用（灰色）。
    """

    def __init__(self, title: str = "bgclick 服务",
                 tooltip: str = "bgclick 服务",
                 icon_path: Optional[str] = None,
                 menu_items: Optional[list[tuple[str, Callable[[], None]]]] = None,
                 on_quit: Optional[Callable[[], None]] = None,
                 on_status: Optional[Callable[[], str]] = None):
        self.title = title
        self.tooltip = tooltip[:127]
        self.icon_path = icon_path
        self.menu_items = menu_items or []
        self.on_quit = on_quit
        self.on_status = on_status
        self.hwnd: Optional[int] = None
        self.hicon = None
        self._nid: Optional[NOTIFYICONDATA] = None
        self._wndproc_ref = None      # 必须持有引用，否则回调被 GC 掉会崩
        self._ready = threading.Event()
        self._taskbar_msg = 0

    # --- 内部 ---
    def _create_icon(self):
        if self.icon_path and os.path.exists(self.icon_path):
            h = user32.LoadImageW(None, self.icon_path, IMAGE_ICON, 0, 0,
                                  LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if h:
                return h
        return user32.LoadIconW(None, wintypes.LPCWSTR(IDI_APPLICATION))

    def _show_menu(self):
        """
        弹出右键菜单。

        ★ 关于 TrackPopupMenu 的三个坑（实测踩过）：

        坑 1：必须 SetForegroundWindow(self.hwnd) —— 否则菜单点了不消失。
              （原代码有，保留）

        坑 2：TrackPopupMenu 返回后必须 PostMessageW(WM_NULL)。
              这是 MSDN 明确要求的：菜单的模态循环退出时，可能还有一条
              鼠标消息在队列里没被处理，导致「下一次」点击落在错误的窗口上，
              或者菜单「粘住」。表现就是「菜单项点了没反应」。

        坑 3：用 TPM_RETURNCMD 时，TrackPopupMenu 直接返回选中项 ID，
              不发送 WM_COMMAND。所以调用方要拿返回值来判断。
        """
        hmenu = user32.CreatePopupMenu()
        if not hmenu:
            return
        try:
            # 状态行（灰色不可点）
            if self.on_status:
                try:
                    user32.AppendMenuW(hmenu, MF_STRING | MF_GRAYED, 0,
                                       f"状态：{self.on_status()}")
                except Exception:
                    pass
                user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)

            ids: dict[int, Callable[[], None]] = {}
            for i, (label, cb) in enumerate(self.menu_items, start=1000):
                if label == "-":
                    user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
                    continue
                flags = MF_STRING
                if label.startswith("!"):
                    flags |= MF_GRAYED
                    label = label[1:]
                user32.AppendMenuW(hmenu, flags, i, label)
                ids[i] = cb

            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))

            # 坑 1：SetForegroundWindow 是必须的，否则菜单点了不消失
            user32.SetForegroundWindow(self.hwnd)

            cmd = user32.TrackPopupMenu(
                hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                pt.x, pt.y, 0, self.hwnd, None)

            # ★ 坑 2：MSDN 明确要求 —— TrackPopupMenu 返回后发一条 WM_NULL，
            #        否则模态循环残留的鼠标消息会让菜单行为异常（点不动 / 粘住）。
            user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)

            # 坑 3：TPM_RETURNCMD 模式下，cmd 就是选中的菜单项 ID（没选返回 0）
            if cmd in ids:
                # ★ 菜单回调异常不能被静默吞掉：弹气泡 + 让上层能看到
                #   注意 notify 本身也可能失败，要独立 try 两层
                try:
                    ids[cmd]()
                except Exception as e:
                    try:
                        self.notify("操作失败", f"{type(e).__name__}: {e}")
                    except Exception:
                        pass
        finally:
            user32.DestroyMenu(hmenu)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAYICON:
            if lparam in (WM_RBUTTONUP, WM_LBUTTONUP):
                self._show_menu()
            elif lparam == WM_LBUTTONDBLCLK:
                # 双击 = 第一个菜单项（一般是「打开状态页/日志」）
                if self.menu_items:
                    try:
                        self.menu_items[0][1]()
                    except Exception as e:
                        try:
                            self.notify("操作失败", f"{type(e).__name__}: {e}")
                        except Exception:
                            pass
            # ★ 不在这里 return 0 把消息吃掉。
            #   WM_TRAYICON 是 Shell_NotifyIcon 的自定义回调消息，
            #   吃掉它会让 TrackPopupMenu 的模态循环拿不到确认。
            #   交给 DefWindowProc 是安全的：它对这个自定义消息没有默认行为，
            #   只会忽略。
        elif msg == WM_COMMAND:
            # 兼容非 TPM_RETURNCMD 的用法（当前代码不用，但留着无害）
            return 0
        elif msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        elif self._taskbar_msg and msg == self._taskbar_msg:
            # 资源管理器重启了，托盘图标需要重新添加
            self._add_icon()
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _add_icon(self):
        """
        把图标加进系统托盘。

        ★ 检查 Shell_NotifyIconW 的返回值。
        之前忽略返回值，导致「以为有托盘其实没创建成功」完全静默 ——
        失败原因常见有：
          * hIcon 是坏的（图标文件损坏，LoadImageW 返回非 NULL 但无效的句柄）
          * hWnd 无效（窗口没创建成功）
          * 结构体 cbSize 与系统版本不匹配（本代码用 V3，Win7+ 均支持）
        失败时抛异常，让上层 bgserver 能写进日志、弹 MessageBox。
        """
        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self.hicon
        nid.szTip = self.tooltip
        self._nid = nid

        ctypes.set_last_error(0)
        ok = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        if not ok:
            err = ctypes.get_last_error()
            self._nid = None
            raise OSError(
                f"Shell_NotifyIconW(NIM_ADD) 失败："
                f"错误码 {err}（hWnd={self.hwnd!r}, hIcon={self.hicon!r}）")

    # --- 公开 API ---
    def notify(self, title: str, text: str) -> None:
        """弹一个气泡提示。"""
        if not self._nid:
            return
        nid = self._nid
        nid.uFlags = NIF_INFO
        nid.szInfoTitle = title[:63]
        nid.szInfo = text[:255]
        nid.dwInfoFlags = 0x00000001  # NIIF_INFO
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP

    def set_tooltip(self, text: str) -> None:
        self.tooltip = text[:127]
        if not self._nid:
            return
        nid = self._nid
        nid.uFlags = NIF_TIP
        nid.szTip = self.tooltip
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP

    def quit(self) -> None:
        """退出托盘（线程安全，可从任意线程调）。"""
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    def run(self) -> None:
        """
        创建托盘并进入消息循环（阻塞，应在主线程调用）。
        """
        hinst = kernel32.GetModuleHandleW(None)
        self.hicon = self._create_icon()

        self._taskbar_msg = user32.RegisterWindowMessageW("TaskbarCreated")

        cls_name = f"bgclick_tray_{os.getpid()}"
        self._wndproc_ref = WNDPROCTYPE(self._wndproc)

        wc = WNDCLASSEX()
        wc.cbSize = ctypes.sizeof(WNDCLASSEX)
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinst
        wc.lpszClassName = cls_name
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            # 1410 = 类已存在，可忽略
            if err != 1410:
                raise OSError(f"RegisterClassExW 失败：{err}")

        # 一个不可见的窗口，只为收托盘消息
        self.hwnd = user32.CreateWindowExW(
            0, cls_name, self.title, 0, 0, 0, 0, 0, None, None, hinst, None)
        if not self.hwnd:
            raise OSError(f"CreateWindowExW 失败：{ctypes.get_last_error()}")

        self._add_icon()
        self._ready.set()

        msg = MSG()
        # GetMessageW 返回 0 = WM_QUIT，-1 = 错误
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0:
                break
            if ret == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        # 收尾：删图标
        if self._nid:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
        if self.on_quit:
            try:
                self.on_quit()
            except Exception:
                pass

    def wait_ready(self, timeout: float = 10.0) -> bool:
        return self._ready.wait(timeout)