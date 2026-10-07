# -*- coding: utf-8 -*-
"""bgkit.bgserver —— 后台点击 / 截图的常驻本地 HTTP 服务"""

from __future__ import annotations

from .config import *  # noqa: F401,F403
from .security import *  # noqa: F401,F403
from .focus import *  # noqa: F401,F403
from .api import *  # noqa: F401,F403
from .logfile import *  # noqa: F401,F403
from .singleton import *  # noqa: F401,F403
from .bootstrap import *  # noqa: F401,F403
from .cli import *  # noqa: F401,F403

# 下划线开头的名字不会被 import * 带出，显式补上（老代码在用）
from .bootstrap import _early_log_candidates  # noqa: F401
from .focus import _find_focus_window  # noqa: F401
from .cli import _main_inner  # noqa: F401
