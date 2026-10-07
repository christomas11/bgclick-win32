# -*- coding: utf-8 -*-
"""bgkit.bgserver.singleton.py —— 单实例锁与「已有实例」探测

从最初的单文件脚本拆出的一节。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

from bgkit import bgclick as bc
import ctypes
import json
from typing import Optional



# --------------------------------------------------------------------------
# 单实例锁
# --------------------------------------------------------------------------


class SingleInstance:
    """
    用命名互斥体保证同一端口只有一个服务实例。

    没有这个的话，用户双击 exe 两次就会出现两个服务抢同一个端口，
    第二个静默失败，用户体验很差（而且托盘出现两个图标）。
    """

    def __init__(self, name: str):
        self.name = name
        self.handle = None

    def acquire(self) -> bool:
        ERROR_ALREADY_EXISTS = 183
        # 用 bc 里已经加载好的 kernel32 / wintypes，别再重复 LoadLibrary
        k32 = bc.kernel32
        wt = bc.wintypes
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
        k32.CreateMutexW.restype = wt.HANDLE
        ctypes.set_last_error(0)
        self.handle = k32.CreateMutexW(None, False, f"Global\\{self.name}")
        err = ctypes.get_last_error()
        if not self.handle:
            return True          # 拿不到互斥体就当没锁，别把服务挡在门外
        return err != ERROR_ALREADY_EXISTS

    def release(self) -> None:
        if self.handle:
            try:
                bc.kernel32.CloseHandle(self.handle)
            except Exception:
                pass
            self.handle = None


def find_existing(port: int) -> Optional[int]:
    """端口上已有服务在跑就返回它的 pid，否则 None。"""
    try:
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return json.loads(resp.read().decode("utf-8")).get("pid")
    except Exception:
        return None
