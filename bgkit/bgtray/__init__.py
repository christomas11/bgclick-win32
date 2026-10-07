# -*- coding: utf-8 -*-
"""bgkit.bgtray —— bgserver 的托盘控制台（纯 ctypes，零第三方依赖）"""

from __future__ import annotations

from .win32tray import *  # noqa: F401,F403
from .shell import *  # noqa: F401,F403
from .app import *  # noqa: F401,F403

# 下划线开头的名字不会被 import * 带出，显式补上（老代码在用）
from .shell import _open_path  # noqa: F401
