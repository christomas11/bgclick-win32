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

# ========================================================================
# 兼容壳：真正的实现已经拆到 bgkit/bgtray/ 下面了。
# 这个文件保留原来的名字和用法，老的调用一律不用改：
#     import bgtray as bc        →  bc.user32 / bc.match_windows / ...
#     python bgtray.py --list       →  命令行照旧
# ========================================================================

from bgkit.bgtray import *  # noqa: F401,F403

# import * 不会带出下划线开头的名字，补上以保持符号面一致
from bgkit.bgtray import _open_path  # noqa: F401


if __name__ == "__main__":
    import sys

    from bgkit.bgtray.cli import main as _main

    sys.exit(_main())
