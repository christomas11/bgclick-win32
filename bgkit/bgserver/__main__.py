# -*- coding: utf-8 -*-
"""python -m bgkit.bgserver —— 等价于 `bgserver`，也等价于 `python bgserver.py`。"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
