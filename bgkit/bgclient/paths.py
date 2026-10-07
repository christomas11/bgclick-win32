# -*- coding: utf-8 -*-
"""bgkit.bgclient.paths.py —— 状态目录与连接信息（token / 端口）

从 bgclient.py 拆出的一节（源文件第 42-132 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

from bgkit import ROOT
import os
import tempfile


HERE = ROOT   # 拆分后指回项目根目录（bgserver.py 就在那儿）
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
TIMEOUT = 30


def state_dir() -> str:
    """
    状态目录（token / 端口 / pid）。

    必须和 bgserver.py 里的 state_dir() **完全一致**（两边用同一套回退链，
    否则客户端和服务会读到不同的 token）。

    回退顺序：
      1. 环境变量 BGCLICK_STATE_DIR（显式指定，最优先）
      2. ~/.bgclick                     （正常情况，用 expanduser("~")，
                                          与 bgserver 保持一致；不依赖 USERPROFILE）
      3. 临时目录 /bgclick-state        （~ 不可写时，比如受限沙箱）
    """
    candidates = []
    env = os.environ.get("BGCLICK_STATE_DIR")
    if env:
        candidates.append(os.path.abspath(env))
    try:
        home = os.path.expanduser("~")
        if home and home != "~":
            candidates.append(os.path.join(home, ".bgclick"))
    except Exception:
        pass
    try:
        candidates.append(os.path.join(tempfile.gettempdir(), "bgclick-state"))
    except Exception:
        pass

    last_err: Exception | None = None
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            # 确认真的能写（makedirs 成功不代表可写，比如目录已存在但只读）
            probe = os.path.join(d, ".write-test")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            return d
        except Exception as e:
            last_err = e
            continue

    raise PermissionError(
        "找不到可写的状态目录。试过：\n  " + "\n  ".join(candidates)
        + f"\n最后一个错误：{last_err}\n"
        "可以用环境变量指定：set BGCLICK_STATE_DIR=D:\\some\\writable\\dir"
    )


# --------------------------------------------------------------------------
# 连接信息
# --------------------------------------------------------------------------


def read_token() -> str:
    try:
        p = os.path.join(state_dir(), "token.txt")
    except Exception as e:
        raise FileNotFoundError(
            f"找不到可写的状态目录，没法存 token：{e}\n"
            "可以用环境变量指定：set BGCLICK_STATE_DIR=D:\\some\\writable\\dir") from e
    if not os.path.exists(p):
        raise FileNotFoundError(
            "找不到 token 文件。服务似乎还没启动过。\n"
            "先运行：python bgserver.py")
    with open(p, "r", encoding="utf-8") as f:
        t = f.read().strip()
    if not t:
        raise FileNotFoundError("token 文件是空的。删掉它再启动服务试试。")
    return t


def read_port() -> int:
    try:
        p = os.path.join(state_dir(), "port.txt")
    except Exception:
        return DEFAULT_PORT
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return int(f.read().strip())
        except Exception:
            pass
    return DEFAULT_PORT
