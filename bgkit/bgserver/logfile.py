# -*- coding: utf-8 -*-
"""bgkit.bgserver.logfile.py —— 日志（无窗口 exe 下看不到 stdout，所以必须落盘）

从 窗口工作.py 拆出的一节（源文件第 970-1014 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

import os
import sys
import threading
import time



# --------------------------------------------------------------------------
# 日志（无窗口 exe 下看不到 stdout，所以必须落盘）
# --------------------------------------------------------------------------


class Logger:
    """
    同时写日志文件和（可用时）stdout。

    打包成 --noconsole 的 exe 后 stdout 是无效句柄，往里写会直接抛异常，
    所以这里要能优雅降级到只写文件。
    """

    def __init__(self, path: str, also_stdout: bool = True):
        self.path = path
        self.also_stdout = also_stdout and self._stdout_ok()
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)

    @staticmethod
    def _stdout_ok() -> bool:
        try:
            if sys.stdout is None:
                return False
            sys.stdout.write("")
            sys.stdout.flush()
            return True
        except Exception:
            return False

    def write(self, text: str = "") -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}"
        with self._lock:
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass
            if self.also_stdout:
                try:
                    print(text, flush=True)
                except Exception:
                    self.also_stdout = False
