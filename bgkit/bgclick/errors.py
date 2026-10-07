# -*- coding: utf-8 -*-
"""bgkit.bgclick.errors.py —— 异常与错误码文案

从 bgclick.py 拆出的一节（源文件第 351-358, 738-746 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

from .win32 import ERROR_ACCESS_DENIED, ERROR_INVALID_WINDOW_HANDLE, ERROR_NOT_ENOUGH_QUOTA



class AppError(Exception):
    """带退出码的业务异常。"""

    def __init__(self, msg: str, code: int = 2):
        super().__init__(msg)
        self.code = code




def describe_error(err: int) -> str:
    table = {
        ERROR_ACCESS_DENIED: "拒绝访问 —— UIPI 拦截（目标权限比本进程高，需要 --elevate）",
        ERROR_INVALID_WINDOW_HANDLE: "无效窗口句柄 —— 窗口可能已经关了",
        ERROR_NOT_ENOUGH_QUOTA: "消息队列已满（目标进程可能卡死了）",
    }
    return table.get(err, f"Windows 错误码 {err}")
