# -*- coding: utf-8 -*-
"""bgkit.bgtray.shell.py —— 打开文件 / 目录（菜单动作的基础）

从 bgtray.py 拆出的一节（源文件第 152-170 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

import os
import subprocess



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
