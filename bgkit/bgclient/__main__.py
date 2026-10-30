# -*- coding: utf-8 -*-
"""python -m bgkit.bgclient —— 等价于 `bgclient`，也等价于 `python bgclient.py`。"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
