# -*- coding: utf-8 -*-
"""bgkit.bgclick.sendinput.py —— SendInput：走系统输入队列（可整批提交、可带扫描码）

从 bgclick.py 拆出的一节（源文件第 832-943, 989-1258 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import time
from typing import Optional
from .errors import AppError
from .geometry import resolve_screen_point
from .keys import EXTENDED_VKS, VK_CONTROL, VK_MENU, VK_SHIFT, key_scancode
from .win32 import HW_BUTTONS, WHEEL_DELTA, user32



def send_input_chords(chords: list[list[int]], repeat: int = 1, interval: float = 0.06,
                      use_scancode: bool = True) -> dict:
    """用 SendInput 依次投递多个组合键（每个组合一批原子提交）。"""
    sent = failed = runs = 0
    last_err = 0
    for r in range(max(1, repeat)):
        for i, vks in enumerate(chords):
            rec = send_chord_input(vks, use_scancode=use_scancode)
            sent += rec["sent"]
            if not rec["ok"]:
                failed += rec["expected"] - rec["sent"]
                last_err = rec["error"]
                break
            runs += 1
            if i < len(chords) - 1:
                time.sleep(max(0.0, interval))
        if failed:
            break
        if repeat > 1:
            time.sleep(max(0.0, interval))
    return {"ok": failed == 0 and sent > 0, "sent": sent, "failed": failed,
            "runs": runs, "repeat": max(1, repeat), "method": "sendinput",
            "scancode": bool(use_scancode), "error": last_err,
            "chords": [[hex(v) for v in c] for c in chords]}


def _key_input_for(vk: int, keyup: bool = False, use_scancode: bool = True) -> INPUT:
    """造一个键盘 INPUT 事件。use_scancode 时只交扫描码（不交虚拟键码）。"""
    scan = key_scancode(vk)
    flags = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VKS else 0
    if keyup:
        flags |= KEYEVENTF_KEYUP
    if use_scancode and scan:
        return _mk_key_input(0, scan, flags | KEYEVENTF_SCANCODE)
    return _mk_key_input(vk, scan, flags)


def send_input_keys_hold(vks: list[int], hold_seconds: float = 0.2, repeat: int = 1,
                         interval: float = 0.06, use_scancode: bool = True) -> dict:
    """
    SendInput 版的「多键同按」：按下批 + 等待 + 抬起批。

    ★ 和消息投递版的区别：这里按下的是**系统层面真实按住的键**，
      期间用户按别的键也不影响；抬起批次保证倒序（先放主键再放修饰键）。
    """
    if not vks:
        raise AppError("按键列表是空的", code=2)
    if not (0 <= hold_seconds <= 3600):
        raise AppError("hold_seconds 必须在 0~3600 秒之间", code=2)

    sent = failed = runs = 0
    for _ in range(max(1, repeat)):
        down = [_key_input_for(vk, False, use_scancode) for vk in vks]
        n_down, _ = _send_inputs(down)
        sent += n_down
        if n_down != len(down):
            failed += len(down) - n_down
            break

        time.sleep(hold_seconds)

        up = [_key_input_for(vk, True, use_scancode) for vk in reversed(vks)]
        n_up, _ = _send_inputs(up)
        sent += n_up
        runs += 1
        if n_up != len(up):
            failed += len(up) - n_up
            break
        if repeat > 1:
            time.sleep(max(0.0, interval))

    return {"ok": failed == 0 and sent > 0, "sent": sent, "failed": failed,
            "runs": runs, "held_seconds": hold_seconds, "method": "sendinput",
            "scancode": bool(use_scancode), "keys": [hex(v) for v in vks]}


def send_input_text(text: str, interval: float = 0.008, use_scancode: bool = True) -> dict:
    """
    用 SendInput 逐字符输入文本。

    ★ 走的是 KEYEVENTF_SCANCODE 路径 —— 只交扫描码，不交虚拟键码。
      这就是「用扫描码」这件事在键盘上的真实含义：让只读底层键盘输入的
      程序（DirectInput / Raw Input 类）也能认账。
    当前键盘布局打不出的字符（中文等）直接跳过并计入 skipped。
    """
    sent = failed = typed = skipped = 0
    for one in text:
        sc = int(user32.VkKeyScanW(one))
        if sc < 0 or (sc & 0xFFFF) == 0xFFFF:
            skipped += 1
            continue
        state = (sc >> 8) & 0xFF
        vks: list[int] = []
        if state & 1:
            vks.append(VK_SHIFT)
        if state & 2:
            vks.append(VK_CONTROL)
        if state & 4:
            vks.append(VK_MENU)
        vks.append(sc & 0xFF)
        rec = send_chord_input(vks, use_scancode=use_scancode)
        sent += rec["sent"]
        if not rec["ok"]:
            failed += rec["expected"] - rec["sent"]
            break
        typed += 1
        time.sleep(max(0.0, interval))
    return {"ok": failed == 0 and sent > 0, "sent": sent, "failed": failed,
            "typed_chars": typed, "skipped_unmappable": skipped,
            "method": "sendinput", "scancode": bool(use_scancode)}




# --------------------------------------------------------------------------
# SendInput —— 走系统输入队列的底层注入（比 mouse_event 更正规、可批量、可带扫描码）
# --------------------------------------------------------------------------
#
# 为什么要有这一层（三档输入方式的真实差别）：
#
#   ① PostMessage     只把消息塞进目标窗口队列。光标不动、焦点不动，最"干净"，
#                     但**鼠标位置是全局状态**，程序一 GetCursorPos 就露馅 ——
#                     所以移动类操作大量失效（点击是事件，反而常常有效）。
#   ② mouse_event     往系统输入队列里塞事件，真动光标，所有程序都吃。
#                     但它不支持"一次提交一批"、也不能指定扫描码。
#   ③ SendInput       ★ 系统输入队列的**正规**入口：一次可以提交一整批事件，
#                     原子性好（拖拽的按下/移动/抬起不会被别的输入插队拆散），
#                     键盘还能用 KEYEVENTF_SCANCODE 只发扫描码。
#
# ★ 关于「扫描码」（我朋友的提示，也是这次调研的重点）：
#   - 扫描码是**键盘**概念。MOUSEINPUT 的字段只有 dx/dy/mouseData/dwFlags/
#     time/dwExtraInfo，压根没有扫描码字段（本机实测确认）。
#   - 键盘的扫描码为什么重要：虚拟键码(VK)会随键盘布局和修饰键状态变化，
#     只读底层输入的 DirectInput/Raw Input 程序对 VK 路径不买账；
#     扫描码不随布局变，所以 SendInput + KEYEVENTF_SCANCODE 能"像真键盘"。
#   - 所以「用扫描码实现鼠标滑动」这个说法在字面上不成立，但**方向是对的**：
#     它指的是「别发消息，走系统输入队列」。鼠标这边的对应物是 SendInput 的
#     MOUSEEVENTF_MOVE + MOUSEEVENTF_ABSOLUTE。
#
# ★ 能力边界（必须说清楚，别让人抱错期望）：
#   SendInput 注入的输入，Raw Input 程序**仍然能识别为注入** ——
#   RAWINPUTHEADER.hDevice 为 NULL，低级钩子能看到 LLMHF_INJECTED 标志。
#   想让输入在内核视角"来自真实硬件"，只有驱动级方案（虚拟 HID 设备 /
#   Interception 这类过滤驱动）。那需要装驱动、要签名、要管理员，跳出本项目范围。
#   所以本层解决的是「程序不认消息投递」，不解决「反作弊识破注入」。

INPUT_MOUSE, INPUT_KEYBOARD, INPUT_HARDWARE = 0, 1, 2

# MOUSEEVENTF 补充标志
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
# KEYEVENTF
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0001, 0x0002, 0x0008


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


def _mk_mouse_input(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = MOUSEINPUT(dx=dx, dy=dy, mouseData=data & 0xFFFFFFFF,
                        dwFlags=flags, time=0, dwExtraInfo=None)
    return inp


def _mk_key_input(vk: int, scan: int, flags: int) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=None)
    return inp


def _send_inputs(inputs: list[INPUT]) -> tuple[int, int]:
    """
    提交一批输入事件。返回 (已发送条数, 错误码)。

    ★ 一次提交一整批而不是逐条发：系统把整批当成一个原子操作处理，
      拖拽的「按下→移动→抬起」不会被用户真实的鼠标动作插进来拆散。
      这是 SendInput 相对 mouse_event 的主要优势。
    """
    n = len(inputs)
    if n == 0:
        return 0, 0
    arr = (INPUT * n)(*inputs)
    ctypes.set_last_error(0)
    sent = int(user32.SendInput(n, arr, ctypes.sizeof(INPUT)))
    err = 0 if sent == n else (ctypes.get_last_error() or 1)
    return sent, err


SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


def _to_absolute(x: int, y: int) -> tuple[int, int]:
    """
    屏幕像素 → SendInput 的绝对坐标（0..65535 归一化）。

    ★ 归一化必须基于**虚拟屏幕**（所有显示器拼起来的那个矩形），
      并且要带 MOUSEEVENTF_VIRTUALDESK，否则多显示器下第二个屏幕的坐标会算错。
      这是 SendInput 和 SetCursorPos 最大的差别：SetCursorPos 收像素，
      SendInput 收的是 65535 比例值。
    """
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = max(1, user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
    vh = max(1, user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
    ax = int(round((x - vx) * 65535.0 / max(1, vw - 1)))
    ay = int(round((y - vy) * 65535.0 / max(1, vh - 1)))
    return max(0, min(65535, ax)), max(0, min(65535, ay))


def send_input_mouse_move(sx: int, sy: int) -> tuple[int, int]:
    """把光标移到屏幕坐标 (sx, sy)，用 SendInput 的绝对移动。"""
    ax, ay = _to_absolute(sx, sy)
    return _send_inputs([_mk_mouse_input(
        MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay)])


def send_input_key(vk: int, keyup: bool = False, use_scancode: bool = True,
                   extended: Optional[bool] = None) -> tuple[int, int]:
    """
    用 SendInput 发一个键盘事件。

    ★ use_scancode=True（默认）走 KEYEVENTF_SCANCODE：只交扫描码，
      不交虚拟键码 —— 这是让「只读底层键盘输入」的程序认账的关键（见本节开头）。
      扫描码由 MapVirtualKeyW 从 VK 换算，扫不出来的（极少数）才回退到 VK。
    """
    scan = key_scancode(vk)
    if extended is None:
        extended = vk in EXTENDED_VKS
    flags = 0
    if keyup:
        flags |= KEYEVENTF_KEYUP
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if use_scancode and scan:
        flags |= KEYEVENTF_SCANCODE
        return _send_inputs([_mk_key_input(0, scan, flags)])
    return _send_inputs([_mk_key_input(vk, scan, flags)])


def send_chord_input(vks: list[int], use_scancode: bool = True) -> dict:
    """
    用 SendInput 投递一次组合键（修饰键 → 主键 → 抬起，倒序）。

    ★ 一次调用提交一整批：按下修饰键 + 主键 + 抬起全部打包，
      中间不会被别的输入插队。这是「多键同按」在底层最可靠的实现。
    """
    if not vks:
        raise AppError("组合键为空", code=2)
    inputs = [_key_input_for(vk, False, use_scancode) for vk in vks]
    # 抬起：倒序（先放主键，再放修饰键），和真人一致
    inputs += [_key_input_for(vk, True, use_scancode) for vk in reversed(vks)]

    sent, err = _send_inputs(inputs)
    return {"ok": sent == len(inputs) and sent > 0, "sent": sent, "expected": len(inputs),
            "error": err, "method": "sendinput", "scancode": bool(use_scancode),
            "chord": [hex(v) for v in vks], "events": len(inputs)}


def send_input_swipe(hwnd: int, points: list[tuple[int, int]], button: Optional[str] = None,
                     restore_cursor: bool = True, batch: bool = True) -> dict:
    """
    SendInput 版的滑动/拖拽。

    和 mouse_swipe_hardware 的区别：
      * 走 SendInput（系统正式入口）而不是 mouse_event；
      * batch=True 时**整段轨迹一次提交**，原子性最好，程序收到的移动序列
        不会被用户真实鼠标事件插队打散（拖拽类交互最怕这个）。
      * batch=False 时逐点提交，中间能 sleep，轨迹在时间上更像人手。

    仍然会真实移动光标（SendInput 的语义就是产生真实输入）。
    """
    if button is not None and button not in HW_BUTTONS:
        raise AppError(f"button 只能是 {list(HW_BUTTONS)}，收到 {button!r}", code=2)

    down_up = HW_BUTTONS.get(button) if button else None
    saved = wintypes.POINT()
    had_saved = bool(user32.GetCursorPos(ctypes.byref(saved)))

    # 屏幕坐标序列
    pts = [resolve_screen_point(hwnd, px, py) for (px, py) in points]

    inputs: list[INPUT] = []
    if down_up:
        inputs.append(_mk_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
                                      | MOUSEEVENTF_VIRTUALDESK, *_to_absolute(*pts[0])))
        inputs.append(_mk_mouse_input(down_up[0]))
        for i, (sx, sy) in enumerate(pts[1:], 1):
            inputs.append(_mk_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
                                          | MOUSEEVENTF_VIRTUALDESK, *_to_absolute(sx, sy)))
        inputs.append(_mk_mouse_input(down_up[1]))
    else:
        for sx, sy in pts:
            inputs.append(_mk_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
                                          | MOUSEEVENTF_VIRTUALDESK, *_to_absolute(sx, sy)))

    if batch:
        sent, err = _send_inputs(inputs)
    else:
        sent = 0
        err = 0
        for one in inputs:
            s, e = _send_inputs([one])
            sent += s
            if not s:
                err = e
                break
            time.sleep(0.008)

    restored = False
    if restore_cursor and had_saved:
        time.sleep(0.03)
        restored = bool(user32.SetCursorPos(saved.x, saved.y))

    return {"ok": sent == len(inputs), "mode": "sendinput-drag" if button else "sendinput-swipe",
            "method": "sendinput", "button": button, "batched": bool(batch),
            "from": list(points[0]), "to": list(points[-1]),
            "steps": len(points) - 1, "sent": sent, "expected": len(inputs),
            "error": err, "cursor_restored": restored}


def send_input_scroll(hwnd: int, x: int, y: int, amount: int, axis: str = "vertical",
                      restore_cursor: bool = True) -> dict:
    """
    SendInput 版滚轮。先把光标移过去（滚轮事件发给光标下的窗口），再批量滚。
    mouseData 用有符号值：向下/向右为负（和 mouse_event 一样）。
    """
    MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
    flag = MOUSEEVENTF_WHEEL if axis == "vertical" else MOUSEEVENTF_HWHEEL
    saved = wintypes.POINT()
    had_saved = bool(user32.GetCursorPos(ctypes.byref(saved)))
    sx, sy = resolve_screen_point(hwnd, x, y)

    inputs = [_mk_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
                              | MOUSEEVENTF_VIRTUALDESK, *_to_absolute(sx, sy))]
    for _ in range(abs(amount)):
        delta = WHEEL_DELTA if amount > 0 else -WHEEL_DELTA
        if axis == "horizontal":
            delta = -delta
        inputs.append(_mk_mouse_input(flag, 0, 0, delta & 0xFFFFFFFF))
    sent, err = _send_inputs(inputs)

    restored = False
    if restore_cursor and had_saved:
        time.sleep(0.03)
        restored = bool(user32.SetCursorPos(saved.x, saved.y))

    return {"ok": sent == len(inputs), "mode": "sendinput-scroll", "method": "sendinput",
            "axis": axis, "amount": amount, "sent": sent, "expected": len(inputs),
            "error": err, "screen_point": [sx, sy], "cursor_restored": restored}
