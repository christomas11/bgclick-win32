# -*- coding: utf-8 -*-
"""bgkit.bgclick.targeting.py —— 找窗口：按标题 / 正则 / 进程名 / 句柄匹配

从 bgclick.py 拆出的一节（源文件第 486-524 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

import re
from .win32 import user32
from .wininfo import describe, iter_top_level_windows, process_name, window_pid, window_title



# --------------------------------------------------------------------------
# 找窗口
# --------------------------------------------------------------------------


def match_windows(args) -> list[dict]:
    """按命令行条件筛出候选窗口，返回 describe() 列表。"""
    if args.hwnd is not None:
        if not user32.IsWindow(args.hwnd):
            return []
        return [describe(args.hwnd)]

    title_pat = None
    if args.title:
        title_pat = re.compile(args.title if args.regex else re.escape(args.title),
                               0 if args.regex else re.IGNORECASE)

    proc_want = (args.process or "").lower()
    found = []
    for hwnd in iter_top_level_windows():
        if args.only_visible and not user32.IsWindowVisible(hwnd):
            continue
        title = window_title(hwnd)
        if title_pat is not None:
            if args.exact:
                if not args.regex and title != args.title:
                    continue
                if args.regex and not title_pat.fullmatch(title):
                    continue
            elif not title_pat.search(title):
                continue
        elif not proc_want:
            continue
        if proc_want and process_name(window_pid(hwnd)).lower() != proc_want:
            continue
        found.append(describe(hwnd))
    return found
