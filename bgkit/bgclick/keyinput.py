# -*- coding: utf-8 -*-
"""bgkit.bgclick.keyinput.py —— 键盘投递：组合键 / 多键同按 / 逐字符真按键

从 bgclick.py 拆出的一节（源文件第 2114-2412 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

import time
from typing import Optional
from .errors import AppError
from .keys import EXTENDED_VKS, VK_CONTROL, VK_LCONTROL, VK_LSHIFT, VK_LWIN, VK_MENU, VK_RCONTROL, VK_RSHIFT, VK_RWIN, VK_SHIFT, WM_CHAR, WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP, _is_alt_vk, _resolve_key_token, key_char, key_scancode, make_key_lparam
from .messaging import _dispatch_key_msg
from .win32 import user32



def _stalled(events: list[dict]) -> bool:
    """一旦有消息被拒（UIPI 等），后面的就不用发了 —— 结果只会一样。"""
    return bool(events) and not events[-1].get("ok")


def send_chord(hwnd: int, vks: list[int], method: str = "post", hold: float = 0.02,
               with_char: Optional[bool] = None) -> dict:
    """
    投递**一次**组合键。vks 的最后一个是主键，前面的都是要同时按住的修饰键。

    with_char：
      True  → 主键按下后补一条 WM_CHAR（保证编辑框真的出字符）
      False → 只发按键消息
      None  → 自动：不含 Ctrl / Alt / Win 时才补（Shift+字母 会补 'A'；
              Ctrl+S 不补，免得给程序塞一个 0x13 控制字符）

    返回 {"ok", "sent", "failed", "events": [...]}
    """
    if not vks:
        raise AppError("组合键为空", code=2)

    main = vks[-1]
    mods = vks[:-1]
    shift = any(v in (VK_SHIFT, VK_LSHIFT, VK_RSHIFT) for v in vks)
    ctrl = any(v in (VK_CONTROL, VK_LCONTROL, VK_RCONTROL) for v in vks)
    alt = any(_is_alt_vk(v) for v in vks)
    win = any(v in (VK_LWIN, VK_RWIN) for v in vks)

    if with_char is None:
        with_char = not (ctrl or alt or win)

    ch = key_char(main, shift=shift, ctrl=ctrl, alt=alt) if with_char else None

    events: list[dict] = []
    sent = 0
    failed = 0
    alt_state = False

    def fire(vk: int, keyup: bool) -> None:
        nonlocal sent, failed
        if alt_state:
            msg = WM_SYSKEYUP if keyup else WM_SYSKEYDOWN
        else:
            msg = WM_KEYUP if keyup else WM_KEYDOWN
        lp = make_key_lparam(key_scancode(vk), vk in EXTENDED_VKS,
                             keyup=keyup, alt_down=alt_state)
        ok, err = _dispatch_key_msg(hwnd, msg, vk, lp, method)
        events.append({"vk": vk, "vk_hex": hex(vk), "msg": hex(msg),
                       "keyup": keyup, "ok": ok, "error": err})
        if ok:
            sent += 1
        else:
            failed += 1

    # 1) 修饰键按下（Alt 要先更新状态：之后的消息得升级成 SYS 版本）
    for vk in mods:
        if _is_alt_vk(vk):
            alt_state = True
        fire(vk, False)
        if _stalled(events):
            break

    if not _stalled(events):
        time.sleep(max(0.0, hold))
        # 2) 主键按下
        fire(main, False)
        # 3) 补 WM_CHAR（走字符通路的控件只认这个）
        if ch and not _stalled(events):
            lp = make_key_lparam(key_scancode(main), main in EXTENDED_VKS, alt_down=alt_state)
            ok, err = _dispatch_key_msg(hwnd, WM_CHAR, ord(ch[0]), lp, method)
            events.append({"vk": main, "vk_hex": hex(main), "msg": hex(WM_CHAR),
                           "char": ch[0], "keyup": False, "ok": ok, "error": err})
            if ok:
                sent += 1
            else:
                failed += 1
        # 4) 主键抬起
        if not _stalled(events):
            fire(main, True)
        time.sleep(max(0.0, hold))

    # 5) 修饰键抬起（倒序，和真实键盘一致）
    for vk in reversed(mods):
        if _stalled(events):
            break
        fire(vk, True)
        if _is_alt_vk(vk):
            alt_state = False

    return {"ok": failed == 0 and sent > 0, "sent": sent, "failed": failed,
            "chord": [hex(v) for v in vks], "with_char": bool(with_char),
            "char": ch, "events": events}


def send_key_sequence(hwnd: int, chords: list[list[int]], method: str = "post",
                      repeat: int = 1, interval: float = 0.06, hold: float = 0.02,
                      with_char: Optional[bool] = None) -> dict:
    """
    投递一串组合键（每个 chord 一次完整的按下-抬起），可重复。
    interval 是「两次组合之间」的间隔，hold 是「修饰键与主键之间」的间隔。
    """
    events: list[dict] = []
    sent = 0
    failed = 0
    runs = 0
    stop = False

    for _ in range(max(1, repeat)):
        if stop:
            break
        for i, vks in enumerate(chords):
            rec = send_chord(hwnd, vks, method=method, hold=hold, with_char=with_char)
            events.extend(rec["events"])
            sent += rec["sent"]
            failed += rec["failed"]
            runs += 1
            if rec["failed"]:
                stop = True          # 失败即停，别把同一个错误刷一屏
                break
            if i < len(chords) - 1:
                time.sleep(max(0.0, interval))
        if not stop and repeat > 1:
            time.sleep(max(0.0, interval))

    return {"ok": failed == 0 and sent > 0, "sent": sent, "failed": failed,
            "runs": runs,
            "chords": [[hex(v) for v in c] for c in chords],
            "repeat": max(1, repeat), "method": method,
            "events": events[-40:]}


def send_text_as_keys(hwnd: int, text: str, method: str = "post", hold: float = 0.015,
                      interval: float = 0.01, mode: str = "auto") -> dict:
    """
    逐字符输入文本。

    ★ 为什么要 mode（真实踩到的 bug）：
      最早这里对每个字符**同时**发 WM_KEYDOWN 和 WM_CHAR，想着「两条路都喂，
      总有一条中」。结果在记事本里打 "Hello!" 出来的是 "Hheelllloo!1" ——
      字符全翻倍。原因：
        * 我们发的 WM_CHAR 带的是正确字符（Shift 也算了，'H' 是 'H'）；
        * 而 WM_KEYDOWN 里的 Shift 是按**规范**发的（我们自己的状态机），
          真实键盘状态里并没有人按着 Shift，于是程序自己翻译出的是 'h'。
      两条通道都被同一类控件吃下 → 大写变小写，而且每个字符出两遍。
      所以「双通道」是错的，必须二选一。

    mode：
      "auto"    （默认）**全部走字符通道（WM_CHAR），每个字符只发一条消息**。
                输出精确：大小写、'!' 这类符号都按你给的原样落地。
                记事本、编辑框、输入框都吃这条 —— 也就是「打字」的默认选择。
      "keys"    全部走按键通道（WM_KEYDOWN/UP），一条 WM_CHAR 都不发。
                给那些直接读虚拟键码、不认 WM_CHAR 的程序（IDE、游戏、画布）。
                ★ 代价：目标自己翻译字符时用的是**真实键盘状态**（没人按着 Shift），
                  "Hello!" 可能落成 "hello1" —— 需要精确文本就别用这个模式。
      "both"    ★ 两条通道都发（老行为，会翻倍）。只有目标只认其中一条、
                你又懒得试的时候碰运气用；用在记事本上必然字符重复。

    当前键盘布局打不出的字符（中文、emoji 等）无论哪种模式都只能走 WM_CHAR。
    """
    if mode not in ("auto", "keys", "both"):
        raise AppError("mode 只能是 auto / keys / both", code=2)

    events: list[dict] = []
    sent = 0
    failed = 0
    typed = 0
    fallback = 0
    via_keys = 0
    via_char = 0

    for one in text:
        if failed:
            break
        sc = int(user32.VkKeyScanW(one))
        # 打不出的字符：只有 WM_CHAR 一条路
        if sc < 0 or (sc & 0xFFFF) == 0xFFFF:
            ok, err = _dispatch_key_msg(hwnd, WM_CHAR, ord(one), 1, method)
            events.append({"char": one, "msg": hex(WM_CHAR), "ok": ok,
                           "error": err, "via": "wm_char"})
            fallback += 1
            via_char += 1
            if ok:
                sent += 1
            else:
                failed += 1
            time.sleep(max(0.0, interval))
            continue

        # --- auto：精确字符通道，一条消息一个字符 ---
        if mode == "auto":
            # lParam 的 bit0-15 是重复次数，这里给 1（和真实按键一致）
            lp = make_key_lparam(0, False, False, repeat=1)
            ok, err = _dispatch_key_msg(hwnd, WM_CHAR, ord(one), lp, method)
            events.append({"char": one, "msg": hex(WM_CHAR), "ok": ok,
                           "error": err, "via": "wm_char"})
            via_char += 1
            typed += 1
            if ok:
                sent += 1
            else:
                failed += 1
            time.sleep(max(0.0, interval))
            continue

        # --- keys / both：按键通道（按 VkKeyScan 的修饰键状态发）---
        state = (sc >> 8) & 0xFF
        vks: list[int] = []
        if state & 1:
            vks.append(VK_SHIFT)
        if state & 2:
            vks.append(VK_CONTROL)
        if state & 4:
            vks.append(VK_MENU)
        vks.append(sc & 0xFF)

        rec = send_chord(hwnd, vks, method=method, hold=hold,
                         with_char=(mode == "both"))
        events.extend(rec["events"])
        sent += rec["sent"]
        failed += rec["failed"]
        typed += 1
        if mode == "both":
            via_char += 1
        else:
            via_keys += 1
        time.sleep(max(0.0, interval))

    return {"ok": failed == 0 and sent > 0, "sent": sent, "failed": failed,
            "typed_chars": typed, "wm_char_fallback": fallback,
            "mode": mode, "via_keys": via_keys, "via_char": via_char,
            "method": method, "events": events[-40:]}


def parse_key_list(items) -> list[int]:
    """
    把 ["ctrl", "shift", "a"] 按**原顺序**解析成虚拟键列表。

    和 parse_chord 的区别：这里不把主键挪到末尾 —— 给「按住 / 松开」用，
    顺序由调用方说了算。
    """
    out: list[int] = []
    for t in items:
        out.extend(_resolve_key_token(str(t)))
    if not out:
        raise AppError("按键列表是空的", code=2)
    return out


def send_keys_state(hwnd: int, vks: list[int], keyup: bool, method: str = "post",
                    include_char: bool = False) -> dict:
    """
    只发「按下」或只发「松开」，不配对 —— 用来实现真正的多键同按：
    先 down [ctrl, shift, a]，隔一会儿再 up [a, shift, ctrl]。

    include_char=True 时，在主键按下后补一条 WM_CHAR（仅 keyup=False 有意义）。
    """
    events: list[dict] = []
    sent = 0
    failed = 0
    alt_state = any(_is_alt_vk(v) for v in vks)   # 松开 Alt 时它自己仍算「Alt 按下中」

    for vk in vks:
        if not keyup and _is_alt_vk(vk):
            alt_state = True
        if alt_state:
            msg = WM_SYSKEYUP if keyup else WM_SYSKEYDOWN
        else:
            msg = WM_KEYUP if keyup else WM_KEYDOWN
        lp = make_key_lparam(key_scancode(vk), vk in EXTENDED_VKS,
                             keyup=keyup, alt_down=alt_state)
        ok, err = _dispatch_key_msg(hwnd, msg, vk, lp, method)
        events.append({"vk": vk, "vk_hex": hex(vk), "msg": hex(msg),
                       "keyup": keyup, "ok": ok, "error": err})
        if ok:
            sent += 1
        else:
            failed += 1
            break                      # 失败即停

    if include_char and not keyup and not failed:
        main = vks[-1]
        shift = any(v in (VK_SHIFT, VK_LSHIFT, VK_RSHIFT) for v in vks)
        ctrl = any(v in (VK_CONTROL, VK_LCONTROL, VK_RCONTROL) for v in vks)
        alt = any(_is_alt_vk(v) for v in vks)
        ch = None if (ctrl or alt) else key_char(main, shift=shift)
        if ch:
            lp = make_key_lparam(key_scancode(main), main in EXTENDED_VKS, alt_down=alt)
            ok, err = _dispatch_key_msg(hwnd, WM_CHAR, ord(ch[0]), lp, method)
            events.append({"vk": main, "vk_hex": hex(main), "msg": hex(WM_CHAR),
                           "char": ch[0], "keyup": False, "ok": ok, "error": err})
            if ok:
                sent += 1
            else:
                failed += 1

    return {"ok": failed == 0 and sent > 0, "sent": sent, "failed": failed,
            "keyup": keyup, "events": events}
