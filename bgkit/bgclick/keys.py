# -*- coding: utf-8 -*-
"""bgkit.bgclick.keys.py —— 键盘：虚拟键码表、组合键解析、lParam 组装

从 bgclick.py 拆出的一节（源文件第 1860-2067, 2085-2113, 2413-2424 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

import argparse
import ctypes
import re
from typing import Optional
from .errors import AppError
from .win32 import user32



# --------------------------------------------------------------------------
# 键盘：虚拟键 / 组合键 / 字符（同样走消息投递，不抢焦点）
# --------------------------------------------------------------------------
#
# 为什么不能只用 WM_CHAR：
#   原来的文本输入是逐字符投 WM_CHAR，只对「真的要字符」的控件（编辑框、
#   输入框）有效。菜单快捷键、IDE、游戏、画布类控件都是直接读 WM_KEYDOWN
#   的虚拟键码，收到 WM_CHAR 一律当没看见。所以必须有真正的键盘通路。
#
# 组合键（Ctrl+S、Shift+A）必须按真实顺序发：
#   修饰键按下 → 主键按下 → 主键抬起 → 修饰键抬起（倒序）
# ★ 顺序错了程序就不认：很多程序只在「主键按下那一刻修饰键仍处于按下状态」
#   时才当快捷键处理。
#
# ★ lParam 不能随手填 0：
#   bit 0-15  重复次数
#   bit 16-23 扫描码（不填的话，方向键/Delete 之类可能被认成小键盘数字）
#   bit 24    扩展键标志（右 Ctrl/Alt、方向键、Home/End/Delete、Win 键…）
#   bit 29    context code（Alt 按下期间为 1，与 WM_SYSKEYxxx 配套）
#   bit 30/31 前一次按键状态 / 转换状态（抬起时都要置 1）
#
# Alt 的坑：Alt 按下期间，Windows 把键盘消息**升级成 SYS 版本**
#   （WM_SYSKEYDOWN / WM_SYSKEYUP），不是 WM_KEYDOWN。所以这里要跟踪 Alt 状态。

WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_CHAR = 0x0102
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105

MAPVK_VK_TO_VSC = 0

# --- 虚拟键码 ---
VK_BACK, VK_TAB, VK_RETURN = 0x08, 0x09, 0x0D
VK_SHIFT, VK_CONTROL, VK_MENU, VK_CAPITAL = 0x10, 0x11, 0x12, 0x14
VK_ESCAPE, VK_SPACE = 0x1B, 0x20
VK_PRIOR, VK_NEXT, VK_END, VK_HOME = 0x21, 0x22, 0x23, 0x24
VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN = 0x25, 0x26, 0x27, 0x28
VK_SNAPSHOT, VK_INSERT, VK_DELETE = 0x2C, 0x2D, 0x2E
VK_LWIN, VK_RWIN, VK_APPS = 0x5B, 0x5C, 0x5D
VK_NUMPAD0 = 0x60
VK_MULTIPLY, VK_ADD, VK_SEPARATOR = 0x6A, 0x6B, 0x6C
VK_SUBTRACT, VK_DECIMAL, VK_DIVIDE = 0x6D, 0x6E, 0x6F
VK_NUMLOCK, VK_SCROLL = 0x90, 0x91
VK_LSHIFT, VK_RSHIFT = 0xA0, 0xA1
VK_LCONTROL, VK_RCONTROL = 0xA2, 0xA3
VK_LMENU, VK_RMENU = 0xA4, 0xA5
# OEM 键（美式布局的位置名，中文键盘位置一致）
VK_OEM_PLUS, VK_OEM_COMMA, VK_OEM_MINUS, VK_OEM_PERIOD = 0xBB, 0xBC, 0xBD, 0xBE
VK_OEM_2, VK_OEM_3 = 0xBF, 0xC0
VK_OEM_4, VK_OEM_5, VK_OEM_6, VK_OEM_7 = 0xDB, 0xDC, 0xDD, 0xDE

# 需要置 lParam bit24（扩展键）的键。不置的后果：程序把方向键认成小键盘数字。
EXTENDED_VKS = frozenset({
    VK_RCONTROL, VK_RMENU, VK_INSERT, VK_DELETE, VK_HOME, VK_END,
    VK_PRIOR, VK_NEXT, VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN,
    VK_NUMLOCK, VK_DIVIDE, VK_SNAPSHOT, VK_LWIN, VK_RWIN, VK_APPS,
})

_KEY_TABLE: dict[str, int] = {
    # 编辑键
    "backspace": VK_BACK, "bs": VK_BACK, "back": VK_BACK,
    "tab": VK_TAB, "enter": VK_RETURN, "return": VK_RETURN, "cr": VK_RETURN,
    "esc": VK_ESCAPE, "escape": VK_ESCAPE, "space": VK_SPACE, "spacebar": VK_SPACE,
    "insert": VK_INSERT, "ins": VK_INSERT, "delete": VK_DELETE, "del": VK_DELETE,
    "home": VK_HOME, "end": VK_END,
    "pageup": VK_PRIOR, "pgup": VK_PRIOR, "pagedown": VK_NEXT, "pgdn": VK_NEXT,
    # 方向键
    "left": VK_LEFT, "up": VK_UP, "right": VK_RIGHT, "down": VK_DOWN,
    # 修饰键
    "shift": VK_SHIFT, "lshift": VK_LSHIFT, "rshift": VK_RSHIFT,
    "ctrl": VK_CONTROL, "control": VK_CONTROL, "ctl": VK_CONTROL,
    "lctrl": VK_LCONTROL, "lcontrol": VK_LCONTROL,
    "rctrl": VK_RCONTROL, "rcontrol": VK_RCONTROL,
    "alt": VK_MENU, "menu": VK_MENU, "lalt": VK_LMENU, "ralt": VK_RMENU,
    "win": VK_LWIN, "lwin": VK_LWIN, "rwin": VK_RWIN, "super": VK_LWIN, "cmd": VK_LWIN,
    "apps": VK_APPS, "contextmenu": VK_APPS,
    # 锁定 / 系统
    "capslock": VK_CAPITAL, "caps": VK_CAPITAL,
    "numlock": VK_NUMLOCK, "scrolllock": VK_SCROLL, "scroll": VK_SCROLL,
    "printscreen": VK_SNAPSHOT, "prtsc": VK_SNAPSHOT, "pause": 0x13, "break": 0x03,
    # 小键盘符号
    "multiply": VK_MULTIPLY, "add": VK_ADD, "subtract": VK_SUBTRACT,
    "decimal": VK_DECIMAL, "divide": VK_DIVIDE, "separator": VK_SEPARATOR,
    # 多媒体
    "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
    "next_track": 0xB0, "prev_track": 0xB1, "stop_media": 0xB2, "play_pause": 0xB3,
    # OEM 符号键（'+' / '-' / '.' / ',' 这些字符本身建议写成 plus/minus/period/comma，
    # 因为 '+' 是组合键的分隔符，直接写 ctrl++ 会被解析坏）
    "plus": VK_OEM_PLUS, "equal": VK_OEM_PLUS, "equals": VK_OEM_PLUS,
    "minus": VK_OEM_MINUS, "comma": VK_OEM_COMMA, "period": VK_OEM_PERIOD,
    "dot": VK_OEM_PERIOD, "slash": VK_OEM_2, "grave": VK_OEM_3, "backtick": VK_OEM_3,
    "bracketleft": VK_OEM_4, "lbracket": VK_OEM_4,
    "backslash": VK_OEM_5, "bracketright": VK_OEM_6, "rbracket": VK_OEM_6,
    "quote": VK_OEM_7, "apostrophe": VK_OEM_7,
}
for _i in range(1, 25):                       # F1 = 0x70 ... F24 = 0x87
    _KEY_TABLE[f"f{_i}"] = 0x6F + _i
for _i in range(10):                          # 小键盘 0-9
    _KEY_TABLE[f"numpad{_i}"] = VK_NUMPAD0 + _i


def _is_alt_vk(vk: int) -> bool:
    return vk in (VK_MENU, VK_LMENU, VK_RMENU)


def _is_modifier_vk(vk: int) -> bool:
    return vk in (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LSHIFT, VK_RSHIFT,
                  VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN)


def _resolve_key_token(token: str) -> list[int]:
    """
    把一个按键名/字符解析成虚拟键序列。

    返回列表是因为一个字符可能需要配修饰键才打得出来：
    "!" → [VK_SHIFT, 0x31]，"a" → [0x41]。
    """
    t = token.strip()
    low = t.lower()
    if not t:
        raise AppError("按键名是空的", code=2)
    if low in _KEY_TABLE:
        return [_KEY_TABLE[low]]
    if len(t) == 1:
        if "a" <= low <= "z":
            return [ord(low.upper())]
        if "0" <= t <= "9":
            return [ord(t)]
        sc = int(user32.VkKeyScanW(t))
        if sc >= 0 and (sc & 0xFFFF) != 0xFFFF:
            out: list[int] = []
            state = (sc >> 8) & 0xFF
            if state & 1:
                out.append(VK_SHIFT)
            if state & 2:
                out.append(VK_CONTROL)
            if state & 4:
                out.append(VK_MENU)
            out.append(sc & 0xFF)
            return out
        raise AppError(f"当前键盘布局打不出字符 {t!r}（可以改用 --text 走字符通道）", code=2)
    if low.startswith("0x"):
        try:
            return [int(low, 16)]
        except ValueError:
            pass
    if t.isdigit():
        return [int(t)]
    raise AppError(
        f"认不出的按键名 {t!r}。可用：a-z / 0-9 / f1-f24 / enter / esc / tab / space / "
        f"backspace / delete / home / end / pageup / pagedown / up / down / left / right / "
        f"ctrl / shift / alt / win / plus / minus / comma / period …，"
        f"也可以直接写虚拟键码（0x41 或 65）", code=2)


def parse_chord(spec: str) -> list[int]:
    """
    解析组合键字符串。返回「按下顺序」的虚拟键列表，最后一个是主键。

      "ctrl+shift+s" → [VK_CONTROL, VK_SHIFT, 0x53]
      "shift+a"      → [VK_SHIFT, 0x41]
      "alt+f4"       → [VK_MENU, 0x73]
      "enter"        → [0x0D]

    ★ 分隔符只有 '+'（两侧空格随意）。想按加号键本身请写 ctrl+plus。
    """
    raw = [t.strip() for t in re.split(r"\+", spec) if t.strip()]
    if not raw:
        raise AppError("组合键是空的", code=2)

    vks: list[int] = []
    for t in raw:
        vks.extend(_resolve_key_token(t))

    # 主键 = 最后一个「非修饰键」；整串都是修饰键时就取最后一个
    main_idx = None
    for i, vk in enumerate(vks):
        if not _is_modifier_vk(vk):
            main_idx = i
    if main_idx is None:
        main_idx = len(vks) - 1

    mods = vks[:main_idx] + vks[main_idx + 1:]
    return mods + [vks[main_idx]]


def make_key_lparam(scancode: int, extended: bool = False, keyup: bool = False,
                    repeat: int = 1, alt_down: bool = False) -> int:
    """按 Windows 的键盘消息规范组装 lParam（位含义见本节开头）。"""
    lp = repeat & 0xFFFF
    lp |= (scancode & 0xFF) << 16
    if extended:
        lp |= 1 << 24
    if alt_down:
        lp |= 1 << 29
    if keyup:
        lp |= (1 << 30) | (1 << 31)
    return lp


def key_scancode(vk: int) -> int:
    """虚拟键码 → 扫描码（填进 lParam 的 bit16-23）。"""
    try:
        sc = int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC))
    except Exception:
        return 0
    return sc & 0xFF




def key_char(vk: int, shift: bool = False, ctrl: bool = False,
             alt: bool = False, caps: bool = False) -> Optional[str]:
    """
    算出「这个键在当前键盘布局下会打出什么字符」。

    用 ToUnicodeEx 而不是 MapVirtualKey，因为只有它认得出 Shift 状态：
    MapVirtualKey(VK_TO_CHAR) 对 'A' 永远返回 'a'，Shift+1 也算不出 '!'。
    0x4 标志是「不要改动内核里的键盘状态」，免得污染用户的真实输入。
    """
    state = (ctypes.c_ubyte * 256)()
    if shift:
        state[VK_SHIFT] = 0x80
    if ctrl:
        state[VK_CONTROL] = 0x80
    if alt:
        state[VK_MENU] = 0x80
    if caps:
        state[VK_CAPITAL] = 0x01
    buf = ctypes.create_unicode_buffer(8)
    try:
        n = int(user32.ToUnicodeEx(vk, key_scancode(vk), state, buf, 8, 0x4, None))
    except Exception:
        return None
    if n >= 1:
        s = buf[:n]
        return s if s else None
    return None                     # 0 = 打不出字符，-1 = 死键




def parse_pair(text: str) -> tuple[int, int]:
    """
    解析 "X,Y"。int(..., 0) 表示接受十进制、0x / 0o / 0b 前缀。
    例：'400,300' / '0x190,0x12C' / '0o620,300'
    """
    try:
        a, b = text.replace("，", ",").split(",")
        return int(a.strip(), 0), int(b.strip(), 0)
    except Exception:
        raise argparse.ArgumentTypeError(f"坐标格式应为 X,Y（可带 0x 前缀），收到：{text!r}")
