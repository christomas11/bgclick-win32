# -*- coding: utf-8 -*-
"""bgkit.bgtray.app.py —— 托盘窗口、消息循环与 TrayApp

从 bgtray.py 拆出的一节（源文件第 171-424 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import threading
from typing import Callable, Optional
from .win32tray import IDI_APPLICATION, IMAGE_ICON, LR_DEFAULTSIZE, LR_LOADFROMFILE, MF_GRAYED, MF_SEPARATOR, MF_STRING, MSG, NIF_ICON, NIF_INFO, NIF_MESSAGE, NIF_TIP, NIM_ADD, NIM_DELETE, NIM_MODIFY, NOTIFYICONDATA, POINT, TPM_RETURNCMD, TPM_RIGHTBUTTON, WM_CLOSE, WM_COMMAND, WM_DESTROY, WM_LBUTTONDBLCLK, WM_LBUTTONUP, WM_NULL, WM_RBUTTONUP, WM_TRAYICON, WNDCLASSEX, WNDPROCTYPE, kernel32, shell32, user32



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
