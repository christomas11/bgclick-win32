# -*- coding: utf-8 -*-
"""bgkit.bgclick.activation.py —— WM_ACTIVATE「假激活」

思路来源：ok-oldking/ok-script 的 PostMessageInteraction.try_activate()
（https://github.com/ok-oldking/ok-script —— ok/device/interaction_methods/post_message.py）。
它在投递每一条鼠标消息之前，先给目标窗口和它的顶层窗口各投一条
WM_ACTIVATE / WA_ACTIVE，让后台窗口「自以为在前台」。

为什么可能有救：
    不少程序在 WM_LBUTTONDOWN / WM_MOUSEMOVE / WM_KEYDOWN 的处理开头会先看一眼
    自己是不是 active window，不是就直接 return —— 后台投递的消息就全被丢掉了。
    真实焦点归谁由系统决定，后台进程改不了；但**消息是投给窗口自己的**，
    它信不信这条 WM_ACTIVATE 由它自己决定。对这类「先检查激活状态」的程序，
    一条纯投递的假消息常常就够用，而且**不抢焦点、不动光标、不调前台**。

★ 边界（别夸大，实测前别当成万能药）：
    * 它不改变系统层面的前台窗口 —— GetForegroundWindow() 还是原来那个；
    * 目标若是在 WM_* 处理里直接读**真实光标位置**（GetCursorPos）或
      **真实按键状态**（GetAsyncKeyState），这条假激活一点用都没有；
    * 判断方式决定成败：用 GetActiveWindow() 自查的**可能**被它改到，
      用 GetForegroundWindow()==自己 判断的改不到（那只有 SetForegroundWindow
      或真输入能救，见 --activate / --method hardware）。

副作用（所以默认关闭，要显式打开）：
    * 窗口可能把标题栏/边框画成「激活」的样子（WM_NCACTIVATE 的锅）；
    * 程序会以为自己在前台，去做前台才做的事（恢复渲染、出声、弹提示）。
"""

from __future__ import annotations

import time

from .messaging import post_checked
from .win32 import user32

# WM_ACTIVATE: wParam = WA_ACTIVE / WA_INACTIVE / WA_CLICKACTIVE，lParam = 被取消激活的窗口
WM_ACTIVATE = 0x0006
WM_NCACTIVATE = 0x0086
WA_INACTIVE, WA_ACTIVE, WA_CLICKACTIVE = 0, 1, 2


def _ancestor_chain(hwnd: int, limit: int = 16) -> list[int]:
    """
    hwnd 自己 + 一路 GetParent 到顶层，用来决定 WM_ACTIVATE 该发给谁。

    为什么要发一整条链：WM_ACTIVATE 是**顶层窗口**概念（系统只给它发），
    但真正收到鼠标消息的往往是某个子控件。两边都发一遍最保险 ——
    子控件自查 GetActiveWindow()、顶层自查自己的激活标志，两边都能覆盖到。
    """
    chain: list[int] = []
    cur = int(hwnd)
    for _ in range(limit):
        if not cur or not user32.IsWindow(cur) or cur in chain:
            break
        chain.append(cur)
        cur = int(user32.GetParent(cur) or 0)
    return chain


def fake_activate(hwnd: int, nc: bool = True, chain: bool = True,
                  settle: float = 0.02) -> dict:
    """
    给 hwnd（默认连带它的祖先链）投递 WM_ACTIVATE / WA_ACTIVE，可选 WM_NCACTIVATE。

    ★ 纯投递，不抢焦点、不动光标。真实前台窗口不变。
    ★ settle：投完先睡一下，让激活先被处理，再发鼠标消息 ——
      PostMessage 是异步的，跨线程的两个窗口不在同一条消息队列里，
      不睡这一下，鼠标消息可能先于激活消息被处理，白干。
      （同一线程的同一队列其实是 FIFO，但跨线程就不保证了，所以统一睡。）

    返回：
      {"ok": 至少一条投递成功, "mode": "fake-activate", "hwnd": ...,
       "targets": [...], "nc": bool, "settle": float,
       "posted": [{"hwnd","msg","ok","error"}...], "error": 最后一条失败的错误码}
    """
    targets = _ancestor_chain(hwnd) if chain else [int(hwnd)]
    posted: list[dict] = []
    ok_any = False
    last_err = 0

    for h in targets:
        ok, err = post_checked(h, WM_ACTIVATE, WA_ACTIVE, 0)
        posted.append({"hwnd": hex(h), "msg": hex(WM_ACTIVATE), "ok": ok, "error": err})
        if ok:
            ok_any = True
        else:
            last_err = err
        if nc:
            # WM_NCACTIVATE(wParam=TRUE)：非客户区（标题栏/边框）按「激活」重画。
            # 有些程序靠它一起判断，也顺手让用户看得出窗口被「假激活」了。
            ok2, err2 = post_checked(h, WM_NCACTIVATE, 1, 0)
            posted.append({"hwnd": hex(h), "msg": hex(WM_NCACTIVATE),
                           "ok": ok2, "error": err2})
            if ok2:
                ok_any = True
            else:
                last_err = err2

    if settle > 0:
        time.sleep(settle)

    return {"ok": ok_any, "mode": "fake-activate", "hwnd": hex(int(hwnd)),
            "targets": [hex(h) for h in targets], "nc": bool(nc),
            "settle": settle, "posted": posted,
            "error": 0 if ok_any else last_err}


def maybe_fake_activate(hwnd: int, enabled, **kwargs) -> dict | None:
    """
    统一入口：enabled 为假就什么都不做（返回 None）。

    各调用点（点击 / 滑动 / 键盘 / 文本）都走它，省得到处写 if ——
    返回值直接塞进结果的 "fake_activate" 字段，好用同一套诊断。
    """
    if not enabled:
        return None
    return fake_activate(hwnd, **kwargs)