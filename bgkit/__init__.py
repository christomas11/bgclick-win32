# -*- coding: utf-8 -*-
"""bgkit —— bgclick 项目的实现包。

四个原本各自几万行的单文件脚本（bgclick.py / bgclient.py / bgtray.py /
bgserver.py）在这里按职责拆成小模块；项目根目录仍保留同名文件作为兼容壳，
所以 `import bgclick as bc`、`bc.user32`、`python bgclick.py --list` 这些
老用法一律照旧。
"""
from __future__ import annotations

import os

__version__ = "1.6.0"

# 包目录：pip 安装后是 site-packages/bgkit，开发树里是 <项目>/bgkit
PKG_DIR = os.path.dirname(os.path.abspath(__file__))

# 项目根目录（bgkit 的上一级）—— 状态文件、图标、入口脚本都相对它定位
ROOT = os.path.dirname(PKG_DIR)


def entry_script(*candidates: str) -> str:
    """
    在项目根目录里找一个入口脚本，返回绝对路径。

    candidates 按优先级给：例如 ("bgserver.py", "窗口工作.py") —— 部署环境里
    叫 bgserver.py，本仓库里叫 窗口工作.py，两个都认。
    """
    for name in candidates:
        path = os.path.join(ROOT, name)
        if os.path.exists(path):
            return path
    return os.path.join(ROOT, candidates[0])


def root_file(*candidates: str) -> str:
    """同 entry_script，语义上强调「根目录里的文件」。"""
    return entry_script(*candidates)


def asset_path(name: str = "bgclick.ico") -> str:
    """
    找一个随包资源（目前只有托盘图标）。

    两份都认：
      1. 项目根目录   —— 开发树、PyInstaller 便携版
      2. bgkit/assets —— pip 安装版（打包时通过 package-data 带进去）

    都找不到也返回包内路径，让调用方自己去处理「图标缺失」，
    而不是悄悄用错别的文件。
    """
    for path in (os.path.join(ROOT, name), os.path.join(PKG_DIR, "assets", name)):
        if os.path.exists(path):
            return path
    return os.path.join(PKG_DIR, "assets", name)


def script_or_module(*candidates: str, module: str) -> list[str]:
    """
    拼「拉起子进程」用的命令头。两种部署形态都要能用：

      * 开发树 / 便携版：根目录有 bgclick.py / bgserver.py，直接喂脚本路径
      * pip 安装版：根目录什么都没有，退回 `python -m bgkit.bgclick`

    之前 entry_script() 无条件返回一个不存在的路径，pip 安装后提权重启、
    服务重启这三条路全都会静默失败 —— 表现为「点了没反应」。
    """
    for name in candidates:
        path = os.path.join(ROOT, name)
        if os.path.exists(path):
            return [path]
    return ["-m", module]
