# -*- coding: utf-8 -*-
"""bgkit.bgserver.bootstrap.py —— 提权重启、致命错误弹窗、早期日志位置

从最初的单文件脚本拆出的一节。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

from bgkit import ROOT
from bgkit import bgclick as bc
from bgkit import entry_script
import os
import subprocess
import sys
import tempfile
import time
import traceback



# --------------------------------------------------------------------------
# 启动
# --------------------------------------------------------------------------


def elevation_argv(argv: list[str]) -> list[str]:
    """提权后要用的参数（去掉提权标记，保留其它）。"""
    out = [a for a in argv if a not in ("--elevate", "--_elevated")]
    return out + ["--_elevated"]


def relaunch_elevated(argv: list[str]) -> int:
    """
    提权重启。返回 0 = 已成功拉起；非 0 = 没成，调用方应回退到不提权继续。

    ★ 实测坑：在沙箱/受限环境里调 ShellExecuteW("runas") 会**返回成功**
      （返回值 > 32），但实际上什么都没启动（没有 UAC 可弹）。
      所以这里返回 0 也不代表对面真的起来了 —— 调用方必须自己探活确认。
    """
    exe = sys.executable
    base = os.path.basename(exe).lower()
    if base.startswith("pythonw"):
        cand = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(cand):
            exe = cand
    if getattr(sys, "frozen", False):
        # ★ 打包成 exe 后 exe 自己就是脚本，不能再把 __file__ 当脚本路径传进去。
        #   （PyInstaller 下 __file__ 是 exe 自己的路径，甚至可能是空串 / 构建机路径）
        #   之前无条件拼 [script] + argv，会让子进程收到一个多余的位置参数，
        #   argparse 直接报 "unrecognized arguments" 并以退出码 2 结束 ——
        #   而 console=False 的 exe 看不到任何输出，表现就是「UAC 点完就闪退」。
        params = subprocess.list2cmdline(elevation_argv(argv))
    else:
        script = entry_script("bgserver.py")
        params = subprocess.list2cmdline([script] + elevation_argv(argv))
    print("正在申请管理员权限…… 屏幕上会弹 UAC，请点「是」。")
    try:
        rc = int(bc.shell32.ShellExecuteW(None, "runas", exe, params, os.getcwd(), 1))
    except Exception as e:
        print(f"提权调用异常：{e}", file=sys.stderr)
        return 5
    if rc <= 32:
        print(f"提权失败（ShellExecuteW 返回 {rc}），改为不提权启动。", file=sys.stderr)
        return 5
    print("已请求以管理员权限启动。")
    return 0




def fatal_report(title: str, text: str) -> None:
    """
    致命错误时，无窗口 exe 里用户什么都看不到 —— 所以：
    1) 尽量写一个日志文件
    2) 弹一个 MessageBox（Windows 上必定可见，不依赖控制台）
    """
    body = f"{text}\n\n" + traceback.format_exc()[-3000:]
    for path in _early_log_candidates():
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] FATAL {title}\n{body}\n")
            body += f"\n\n日志已写入：{path}"
            break
        except Exception:
            continue
    try:
        bc.user32.MessageBoxW(None, body[:2000], title, 0x00000010)  # MB_ICONERROR
    except Exception:
        pass


def _early_log_candidates() -> list[str]:
    """还没确定状态目录时，先把日志往这些地方试。"""
    out = []
    env = os.environ.get("BGCLICK_STATE_DIR")
    if env:
        out.append(os.path.join(env, "bgserver.log"))
    try:
        home = os.path.expanduser("~")
        if home and home != "~":
            out.append(os.path.join(home, ".bgclick", "bgserver.log"))
            out.append(os.path.join(home, "bgserver.log"))
    except Exception:
        pass
    try:
        out.append(os.path.join(tempfile.gettempdir(), "bgserver.log"))
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        out.append(os.path.join(os.path.dirname(sys.executable), "bgserver.log"))
    out.append(os.path.join(ROOT, "bgserver.log"))
    return out
