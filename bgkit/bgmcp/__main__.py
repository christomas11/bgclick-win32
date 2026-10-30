# -*- coding: utf-8 -*-
"""python -m bgkit.bgmcp —— 等价于 `bgmcp`，启动 stdio MCP server。"""

from __future__ import annotations

import sys

from .server import main

if __name__ == "__main__":
    sys.exit(main())
