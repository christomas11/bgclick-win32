# -*- coding: utf-8 -*-
"""python -m bgkit —— 列出所有可用的子入口。

单独跑 `python -m bgkit` 不会干任何事，只是提醒你该跑哪个模块。
"""
from __future__ import annotations

import sys

from . import __version__

USAGE = f"""bgkit {__version__} —— 后台操作 Windows 窗口（不抢光标、不抢焦点）

四个子入口：
  python -m bgkit.bgclick   list / scan / click / swipe / shot / uia ...
  python -m bgkit.bgserver  常驻 HTTP 服务（给 agent、脚本、别的语言调用）
  python -m bgkit.bgclient  服务客户端（连已启动的服务）
  python -m bgkit.bgtray    托盘控制台

装了 pip 包之后等价的命令：
  bgclick / bgserver / bgclient / bgtray
"""


def main() -> int:
    print(USAGE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
