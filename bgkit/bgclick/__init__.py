# -*- coding: utf-8 -*-
"""bgkit.bgclick —— bgclick 核心库：Win32 后台点击 / 键盘 / 鼠标 / 截图"""

from __future__ import annotations

from .win32 import *  # noqa: F401,F403
from .errors import *  # noqa: F401,F403
from .wininfo import *  # noqa: F401,F403
from .targeting import *  # noqa: F401,F403
from .geometry import *  # noqa: F401,F403
from .integrity import *  # noqa: F401,F403
from .messaging import *  # noqa: F401,F403
from .sendinput import *  # noqa: F401,F403
from .keys import *  # noqa: F401,F403
from .keyinput import *  # noqa: F401,F403
from .clicking import *  # noqa: F401,F403
from .mouse import *  # noqa: F401,F403
from .screen import *  # noqa: F401,F403
from .elevation import *  # noqa: F401,F403
from .cli import *  # noqa: F401,F403

# UIA 元素查询：名字太通用（walk / find / element_from_hwnd），不做 import *，
# 以模块形式暴露，用 bc.uia.walk(...) 调用。
from . import uia  # noqa: F401

# 下划线开头的名字不会被 import * 带出，显式补上（老代码在用）
from .screen import _HGDI_ERROR  # noqa: F401
from .sendinput import _INPUTUNION  # noqa: F401
from .keys import _KEY_TABLE  # noqa: F401
from .mouse import _clamp_int  # noqa: F401
from .messaging import _dispatch_key_msg  # noqa: F401
from .elevation import _elevation_command  # noqa: F401
from .integrity import _integrity_from_token  # noqa: F401
from .keys import _is_alt_vk  # noqa: F401
from .keys import _is_modifier_vk  # noqa: F401
from .sendinput import _key_input_for  # noqa: F401
from .sendinput import _mk_key_input  # noqa: F401
from .sendinput import _mk_mouse_input  # noqa: F401
from .mouse import _mouse_move_msg  # noqa: F401
from .mouse import _path_from_spec  # noqa: F401
from .screen import _png_chunk  # noqa: F401
from .elevation import _python_for_elevation  # noqa: F401
from .keys import _resolve_key_token  # noqa: F401
from .sendinput import _send_inputs  # noqa: F401
from .keyinput import _stalled  # noqa: F401
from .sendinput import _to_absolute  # noqa: F401
