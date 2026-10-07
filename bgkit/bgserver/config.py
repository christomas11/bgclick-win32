# -*- coding: utf-8 -*-
"""bgkit.bgserver.config.py —— 运行配置：状态目录 / token / 端口 / pid

从最初的单文件脚本拆出的一节。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

import os
import secrets
import tempfile
from typing import Optional


VERSION = "1.4.0"
DEFAULT_PORT = 8765

# 允许截图落盘的根目录（启动时填充为绝对路径）
ALLOWED_SHOT_ROOTS: list[str] = []


# --------------------------------------------------------------------------
# 状态目录 / token
# --------------------------------------------------------------------------


def state_dir() -> str:
    """
    状态目录（token / 端口 / pid）。

    必须和 bgclient.py 里的 state_dir() **完全一致**（同一套回退链），
    否则客户端和服务会读到不同的 token。

    回退顺序：
      1. 环境变量 BGCLICK_STATE_DIR（显式指定，最优先）
      2. ~/.bgclick                     （正常情况，用 expanduser("~")，
                                          不用 USERPROFILE，避免与 client 分叉）
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

    last_err: Optional[Exception] = None
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
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
        + f"\n最后一个错误：{last_err}")


def token_path() -> str:
    return os.path.join(state_dir(), "token.txt")


def port_path() -> str:
    return os.path.join(state_dir(), "port.txt")


def pid_path() -> str:
    return os.path.join(state_dir(), "server.pid")


def load_or_create_token(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    p = token_path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                t = f.read().strip()
            if t:
                return t
        except Exception:
            pass
    t = secrets.token_urlsafe(32)
    with open(p, "w", encoding="utf-8") as f:
        f.write(t)
    try:
        os.chmod(p, 0o600)  # 仅当前用户可读写
    except Exception:
        pass
    return t


def set_allowed_shot_roots(roots) -> None:
    """
    就地更新「允许截图落盘的根目录」。

    ★ 用切片赋值而不是重新绑定 ALLOWED_SHOT_ROOTS：
      security.resolve_shot_path 里写的是 `from .config import ALLOWED_SHOT_ROOTS`，
      拿到的是同一个列表对象的引用。重新绑定只改本模块的名字，那边看不到，
      结果是截图全部被判「路径越界」—— 而且是静默的，很难查。
    """
    ALLOWED_SHOT_ROOTS[:] = list(dict.fromkeys(os.path.abspath(r) for r in roots))
