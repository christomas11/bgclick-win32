# -*- coding: utf-8 -*-
"""bgkit.bgclient —— bgserver 的命令行客户端（零依赖，给 skill 当当入口）"""

from __future__ import annotations

from .paths import *  # noqa: F401,F403
from .bootstrap import *  # noqa: F401,F403
from .http import *  # noqa: F401,F403
from .cli import *  # noqa: F401,F403

# 下划线开头的名字不会被 import * 带出，显式补上（老代码在用）
from .bootstrap import _ping  # noqa: F401
