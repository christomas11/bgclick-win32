# -*- coding: utf-8 -*-
"""bgkit —— bgclick 项目的实现包。

四个原本各自几万行的单文件脚本（bgclick.py / bgclient.py / bgtray.py /
bgserver.py）在这里按职责拆成小模块；项目根目录仍保留同名文件作为兼容壳，
所以 `import bgclick as bc`、`bc.user32`、`python bgclick.py --list` 这些
老用法一律照旧。
"""
from __future__ import annotations

import os

# 项目根目录（bgkit 的上一级）—— 状态文件、图标、入口脚本都相对它定位
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def entry_script(*candidates: str) -> str:
    """
    在项目根目录里找一个入口脚本，返回绝对路径。

    candidates 按优先级给：例如 ("bgserver.py",) —— 部署环境里可能改了名，
    按顺序试一遍，都不在就返回第一个。
    """
    for name in candidates:
        path = os.path.join(ROOT, name)
        if os.path.exists(path):
            return path
    return os.path.join(ROOT, candidates[0])


def root_file(*candidates: str) -> str:
    """同 entry_script，语义上强调「根目录里的文件」。"""
    return entry_script(*candidates)
