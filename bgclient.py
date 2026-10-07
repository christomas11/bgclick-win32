#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bgclient.py —— bgserver 的命令行客户端（也给 agent skill 当调用入口）

零依赖（纯标准库），所以 skill 里直接 subprocess 调它就行。

常用
----
  python bgclient.py health                       # 服务活着吗、是不是管理员
  python bgclient.py windows                      # 列出窗口（含能否点击）
  python bgclient.py click --title "记事本" --pos 400,300
  python bgclient.py shot --title "记事本" --out shots/note.png
  python bgclient.py text --title "记事本" --text "hello" --enter
  python bgclient.py key  --title "记事本" --keys ctrl+shift+s     # 组合键
  python bgclient.py key  --title "记事本" --hold ctrl+shift+a     # 多键同按
  python bgclient.py mouse --title "记事本" --delta 0,-500         # 向上滑动
  python bgclient.py mouse --title "记事本" --scroll -5            # 向下滚 5 格
  python bgclient.py shutdown                     # 停服务

自动化友好
----------
  * 退出码：0 成功，1 用法/未找到，2 参数错误，3 服务未启动，4 权限被拒
  * --json 输出机器可读结果（默认就是 JSON，其实是给 agent 用的）
  * 所有失败都带 {"ok": false, "error": "..."} 而不是抛栈

供 skill 调用的推荐形态：直接 import 这个文件，或者 subprocess 跑它。
"""

# ========================================================================
# 兼容壳：真正的实现已经拆到 bgkit/bgclient/ 下面了。
# 这个文件保留原来的名字和用法，老的调用一律不用改：
#     import bgclient as bc        →  bc.user32 / bc.match_windows / ...
#     python bgclient.py --list       →  命令行照旧
# ========================================================================

from bgkit.bgclient import *  # noqa: F401,F403

# import * 不会带出下划线开头的名字，补上以保持符号面一致
from bgkit.bgclient import _ping  # noqa: F401


if __name__ == "__main__":
    import sys

    from bgkit.bgclient.cli import main as _main

    sys.exit(_main())
