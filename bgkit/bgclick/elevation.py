# -*- coding: utf-8 -*-
"""bgkit.bgclick.elevation.py —— 提权：UAC 重启与「提权有没有用」的判定

从 bgclick.py 拆出的一节（源文件第 2633-2719 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

from bgkit import entry_script
import os
import subprocess
import sys
from typing import Optional
from .integrity import process_integrity, self_integrity
from .win32 import ERROR_ACCESS_DENIED, shell32
from .wininfo import window_pid



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

    script = entry_script("bgclick.py")
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
