# -*- coding: utf-8 -*-
"""bgkit.bgclick.integrity.py —— 进程完整性级别（UIPI 的判定依据）

从 bgclick.py 拆出的一节（源文件第 679-737 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from typing import Optional
from .win32 import INTEGRITY_LEVELS, PROCESS_QUERY_LIMITED_INFORMATION, TOKEN_MANDATORY_LABEL, TOKEN_QUERY, TokenIntegrityLevel, advapi32, kernel32



def _integrity_from_token(token) -> Optional[int]:
    """从令牌读出完整性级别 RID（如 0x1000 = Low）。读不到返回 None。"""
    need = wintypes.DWORD(0)
    advapi32.GetTokenInformation(token, TokenIntegrityLevel, None, 0, ctypes.byref(need))
    if need.value == 0:
        return None
    buf = ctypes.create_string_buffer(need.value)
    if not advapi32.GetTokenInformation(token, TokenIntegrityLevel, buf, need.value,
                                        ctypes.byref(need)):
        return None
    label = ctypes.cast(buf, ctypes.POINTER(TOKEN_MANDATORY_LABEL)).contents
    sid = label.Label.Sid
    if not sid:
        return None
    cnt = advapi32.GetSidSubAuthorityCount(sid)
    if not cnt:
        return None
    n = cnt.contents.value
    if n == 0:
        return None
    sub = advapi32.GetSidSubAuthority(sid, n - 1)  # 最后一个子权威 = 完整性 RID
    return int(sub.contents.value) if sub else None


def self_integrity() -> Optional[int]:
    """本进程的完整性级别 RID。"""
    h = kernel32.GetCurrentProcess()
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        return _integrity_from_token(token)
    finally:
        kernel32.CloseHandle(token)


def process_integrity(pid: int) -> Optional[int]:
    """目标进程的完整性级别 RID。"""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            return _integrity_from_token(token)
        finally:
            kernel32.CloseHandle(token)
    finally:
        kernel32.CloseHandle(h)


def integrity_name(rid: Optional[int]) -> str:
    if rid is None:
        return "读不到"
    return INTEGRITY_LEVELS.get(rid, f"未知（RID 0x{rid:x}）")
