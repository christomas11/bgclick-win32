# -*- coding: utf-8 -*-
"""python -m bgkit.bgtray —— 等价于 `bgtray`，也等价于 `python bgtray.py`。"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
