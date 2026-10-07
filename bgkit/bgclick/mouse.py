# -*- coding: utf-8 -*-
"""bgkit.bgclick.mouse.py —— 鼠标滑动 / 拖拽 / 滚轮

从 bgclick.py 拆出的一节（源文件第 1259-1281, 1283-1859 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import time
from typing import Optional
from .errors import AppError
from .geometry import deepest_child_at, make_lparam, resolve_screen_point, window_dimensions
from .messaging import _dispatch_key_msg, post_checked
from .sendinput import send_input_scroll, send_input_swipe
from .win32 import BUTTONS, ERROR_ACCESS_DENIED, HW_BUTTONS, SMTO_ABORTIFHUNG, SMTO_BLOCK, WHEEL_DELTA, WM_MOUSEMOVE, user32



# --------------------------------------------------------------------------
# 鼠标滑动 / 拖拽 / 滚轮（同样是消息投递，不抢光标）
# --------------------------------------------------------------------------
#
# 为什么滑动不是「发一条消息」就完事：
#   拖动类交互（滑块、列表内容、地图、画布）都是靠**连续的 WM_MOUSEMOVE**
#   累积出来的。中间不补点，程序看到的就是「起点按下 → 瞬移到终点 → 抬起」，
#   要么当没发生，要么判成 0 距离。所以必须按步数插值，一步步走过去。
#
# ★ 拖动期间每条移动消息的 wParam 必须带 MK_LBUTTON（按键状态位）。
#   不带的话程序认为「按键已经松了」，拖动直接断在原地 —— 这是最常见的坑。
#   同理，拖右键要带 MK_RBUTTON。
#
# ★ 坐标是**客户区坐标**，插值时按客户区线性走。
#   如果目标内部还有自己的滚动偏移（画布类控件），程序会自己换算，
#   我们只管把客户区坐标走对。
#
# ★ 同样受 UIPI 约束：低完整性级别发不进高完整性级别的窗口。
#   消息被拒（错误码 5）时立即停手，不会把剩下的步数白跑一遍。

MK_SHIFT, MK_CONTROL = 0x0004, 0x0008




def _clamp_int(value, lo: int, hi: int, name: str, default: int) -> int:
    """整数范围校验。服务端有自己的 clamp_int，这里是库内的等价物，避免跨文件依赖。"""
    if value is None:
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise AppError(f"{name} 必须是整数，收到 {value!r}", code=2)
    if n < lo or n > hi:
        raise AppError(f"{name} 超出允许范围 [{lo}, {hi}]，收到 {n}", code=2)
    return n


def _mouse_move_msg(hwnd: int, x: int, y: int, mk: int, method: str) -> tuple[bool, int]:
    """投递一条带按键状态的 WM_MOUSEMOVE。"""
    lp = make_lparam(x, y)
    if method == "send":
        out = ctypes.c_size_t(0)
        ctypes.set_last_error(0)
        res = user32.SendMessageTimeoutW(hwnd, WM_MOUSEMOVE, mk, lp,
                                         SMTO_ABORTIFHUNG | SMTO_BLOCK, 1000,
                                         ctypes.byref(out))
        err = ctypes.get_last_error()
        if res == 0 and err:
            return False, err
        return True, 0
    return post_checked(hwnd, WM_MOUSEMOVE, mk, lp)


def plan_path(x0: int, y0: int, x1: int, y1: int, steps: int,
              ease: bool = False) -> list[tuple[int, int]]:
    """
    把一段直线拆成 steps 个点（不含起点，含终点）。

    steps=0 时返回空列表，调用方自己补一条到终点的移动。
    ease=True 用 smoothstep 平滑速度（起步慢、中间快、收尾慢），
    比匀速更像人手 —— 有些程序就是这么判断「像不像真人操作」的。

    ★ 取整用 round 而不是 int：int 会一路向下取整，短距离滑动
      （比如 10 像素分 20 步）会全部塌到起点，滑动等于没发生。
    """
    if steps <= 0:
        return []
    pts: list[tuple[int, int]] = []
    for i in range(1, steps + 1):
        t = i / steps
        if ease:
            t = t * t * (3.0 - 2.0 * t)      # smoothstep
        px = round(x0 + (x1 - x0) * t)
        py = round(y0 + (y1 - y0) * t)
        if pts and pts[-1] == (px, py):
            continue                          # 去重：重复点对程序没有意义
        pts.append((px, py))
    if not pts or pts[-1] != (x1, y1):
        pts.append((x1, y1))                  # 终点必须精确落在目标上
    return pts


def _path_from_spec(hwnd: int, start: tuple[int, int], body: dict) -> tuple[list[tuple[int, int]], dict]:
    """
    把请求里的路径写法解析成点序列。支持四种：

      dx/dy        相对滑动（最常用）：dx=0, dy=-500 → 向上滑 500 像素
      to           滑到绝对客户区坐标：to=[400, 300]
      path         一串途经点：[[100,100],[300,300],[500,200]]
      pattern      手势名：line / up / down / left / right / circle / square / zigzag
                   （up/down 这类就是「方向 + 一段固定距离」的语法糖，也可以再配 distance）

    返回 (点序列, 回显用的元信息)。
    """
    cw, ch = window_dimensions(hwnd, client_only=True)
    x0, y0 = start

    def clamp(p: tuple[int, int]) -> tuple[int, int]:
        return max(0, min(int(p[0]), cw - 1)), max(0, min(int(p[1]), ch - 1))

    distance = int(body.get("distance", 300))
    if not (1 <= distance <= 20000):
        raise AppError("distance 必须在 1~20000 像素之间", code=2)

    if body.get("path") is not None:
        raw = body["path"]
        if not isinstance(raw, list) or not raw:
            raise AppError("path 必须是坐标数组，如 [[100,100],[300,300]]", code=2)
        if len(raw) > 128:
            raise AppError("path 最多 128 个点", code=2)
        pts = []
        for item in raw:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise AppError(f"path 里的点必须是 [x, y]，收到 {item!r}", code=2)
            try:
                pts.append(clamp((int(item[0]), int(item[1]))))
            except (TypeError, ValueError):
                raise AppError(f"path 里的坐标必须是整数，收到 {item!r}", code=2)
        return pts, {"mode": "path", "points": len(pts)}

    if body.get("to") is not None:
        to = body["to"]
        if not isinstance(to, (list, tuple)) or len(to) != 2:
            raise AppError("to 必须是 [x, y]", code=2)
        try:
            dest = clamp((int(to[0]), int(to[1])))
        except (TypeError, ValueError):
            raise AppError(f"to 的坐标必须是整数，收到 {to!r}", code=2)
        steps = _clamp_int(body.get("steps"), 1, 5000, "steps", 30)
        return plan_path(x0, y0, dest[0], dest[1], steps,
                         ease=bool(body.get("ease"))), {"mode": "to", "to": list(dest)}

    pattern = body.get("pattern")
    if pattern:
        name = str(pattern).strip().lower()
        cx, cy = x0, y0
        pts: list[tuple[int, int]] = []
        if name in ("line", "up", "down", "left", "right"):
            vec = {"up": (0, -distance), "down": (0, distance),
                   "left": (-distance, 0), "right": (distance, 0),
                   "line": (0, -distance)}[name]
            if name == "line":      # line 用 dx/dy；没给就默认向上
                vec = (int(body.get("dx", 0)), int(body.get("dy", -distance)))
            steps = _clamp_int(body.get("steps"), 1, 5000, "steps", 30)
            dest = clamp((cx + vec[0], cy + vec[1]))
            pts = plan_path(cx, cy, dest[0], dest[1], steps, ease=bool(body.get("ease")))
        elif name in ("circle", "square", "zigzag"):
            points = _clamp_int(body.get("points"), 3, 720, "points",
                                72 if name == "circle" else (4 if name == "square" else 8))
            r = max(4, distance // 2)
            if name == "circle":
                import math
                for i in range(1, points + 1):
                    a = 2 * math.pi * i / points
                    pts.append(clamp((cx + r * math.cos(a), cy + r * math.sin(a))))
            elif name == "square":
                d = distance
                corners = [(cx + d, cy), (cx + d, cy + d), (cx, cy + d), (cx, cy)]
                for i in range(1, points + 1):
                    t = i / points * 4
                    seg = min(int(t), 3)
                    f = t - seg
                    a, b = corners[seg], corners[(seg + 1) % 4]
                    pts.append(clamp((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)))
            else:  # zigzag：左右来回扫，像刷列表
                seg = max(2, points // 2)
                for i in range(1, points + 1):
                    t = i / points
                    px = cx + (distance if i % 2 else -distance) * t
                    py = cy - distance * t
                    pts.append(clamp((px, py)))
        else:
            raise AppError(
                f"不认识的 pattern {pattern!r}。可用：line / up / down / left / right / "
                f"circle / square / zigzag", code=2)
        # 手势类默认回到起点：圆/方/锯齿在拖动场景下会把内容拖偏，
        # 走完一圈回到原处更安全（to_start=false 可以关掉）
        if name in ("circle", "square", "zigzag") and body.get("to_start", True):
            pts.append((x0, y0))
        return pts, {"mode": "pattern", "pattern": name, "points": len(pts)}

    # 默认：相对滑动
    dx = body.get("dx")
    dy = body.get("dy")
    if dx is None and dy is None:
        raise AppError("得给一种滑动：dx/dy、to、path 或 pattern", code=2)
    try:
        dx = int(dx or 0)
        dy = int(dy or 0)
    except (TypeError, ValueError):
        raise AppError("dx / dy 必须是整数", code=2)
    if dx == 0 and dy == 0:
        raise AppError("dx 和 dy 都是 0，滑动没有意义", code=2)
    steps = _clamp_int(body.get("steps"), 1, 5000, "steps", 30)
    dest = clamp((x0 + dx, y0 + dy))
    if dest != (x0 + dx, y0 + dy):
        dx, dy = dest[0] - x0, dest[1] - y0
    return plan_path(x0, y0, dest[0], dest[1], steps,
                     ease=bool(body.get("ease"))), {"mode": "delta", "dx": dx, "dy": dy}


def mouse_scroll(hwnd: int, x: int, y: int, amount: int, axis: str = "vertical",
                 method: str = "post", delay: float = 0.03) -> dict:
    """
    滚轮。amount 单位是「格」，正数 = 向上 / 向左，负数 = 向下 / 向右。

    竖滚发 WM_MOUSEWHEEL，横滚发 WM_MOUSEHWHEEL —— 横滚不是所有控件都支持，
    不支持的就完全没反应（不会报错，别以为是代码坏了）。

    ★ 滚轮的 wParam 里**高 16 位**是增量、低 16 位是按键状态，
      和别的鼠标消息正好相反，很容易写反。lParam 用的是**屏幕坐标**，
      不是客户区坐标（这也是滚轮消息和点击消息的区别之一）。
    """
    if axis not in ("vertical", "horizontal"):
        raise AppError("axis 只能是 vertical / horizontal", code=2)
    if not isinstance(amount, int) or amount == 0:
        raise AppError("amount 必须是非 0 整数（正数向上/向左，负数向下/向右）", code=2)
    if abs(amount) > 1000:
        raise AppError("amount 绝对值上限 1000", code=2)

    msg = 0x020A if axis == "vertical" else 0x020E
    sx, sy = resolve_screen_point(hwnd, x, y)
    lp = make_lparam(sx, sy)              # ★ 屏幕坐标
    events: list[dict] = []
    sent = failed = 0

    for _ in range(abs(amount)):
        delta = WHEEL_DELTA if amount > 0 else -WHEEL_DELTA
        if axis == "horizontal":
            delta = -delta                # 横滚方向相反：正 = 向左
        wp = ((delta & 0xFFFF) << 16) | 0  # 高 16 位增量，低 16 位按键状态
        ok, err = _dispatch_key_msg(hwnd, msg, wp, lp, method)
        events.append({"msg": hex(msg), "delta": delta, "ok": ok, "error": err})
        if ok:
            sent += 1
        else:
            failed += 1
            break
        time.sleep(max(0.0, delay))

    return {"ok": failed == 0 and sent > 0, "mode": "scroll", "axis": axis,
            "amount": amount, "sent": sent, "failed": failed,
            "screen_point": [sx, sy], "events": events[-20:]}


def mouse_drag(hwnd: int, points: list[tuple[int, int]], button: str = "left",
               method: str = "post", hold: float = 0.03, delay: float = 0.015,
               release: bool = True, deep: bool = True) -> dict:
    """
    按住一个键沿 points 走完全程。points[0] 是起点。

    ★ 拖动期间每条 WM_MOUSEMOVE 都带 MK_xxx（按键状态位）——
      不带就等于中途松手，拖动会断在起点，这是最经典的坑。

    投递顺序：
      移动到起点 → 按下 → 逐点移动（带 MK）→ 抬起
    deep=True 时和点击一样会逐层钻到最深的子控件。
    """
    if len(points) < 2:
        raise AppError("拖动至少要有起点和终点两个点", code=2)
    if button not in BUTTONS:
        raise AppError(f"button 只能是 {list(BUTTONS)}，收到 {button!r}", code=2)
    if method not in ("post", "send"):
        raise AppError("拖动只支持 post / send（hardware 真拖拽请用 bgclick.py CLI）", code=2)

    down, up, mk = BUTTONS[button]
    events: list[dict] = []
    sent = failed = 0
    # 先决定发给谁：默认钻到最深的子控件（标准控件都是子窗口，发给顶层等于石沉大海）。
    # ★ 只钻一次，起点定了就不再改 —— 拖动途中重新钻取会换成别的控件，把拖拽打断。
    send_to = hwnd
    if deep:
        send_to, _, _, _ = deepest_child_at(hwnd, points[0][0], points[0][1])

    def emit(msg: int, wp: int, lp: int, tag: str) -> bool:
        nonlocal sent, failed
        ok, err = _dispatch_key_msg(send_to, msg, wp, lp, method)
        events.append({"msg": hex(msg), "wparam": hex(wp), "lparam": hex(lp),
                       "ok": ok, "error": err, "tag": tag, "target": hex(send_to)})
        if ok:
            sent += 1
            return True
        failed += 1
        return False

    if not emit(WM_MOUSEMOVE, 0, make_lparam(*points[0]), "move-to-start"):
        return {"ok": False, "mode": "drag", "error": events[-1]["error"],
                "sent": sent, "failed": failed, "events": events[-20:]}
    time.sleep(max(0.0, hold))
    if not emit(down, mk, make_lparam(*points[0]), "down"):
        return {"ok": False, "mode": "drag", "error": events[-1]["error"],
                "sent": sent, "failed": failed, "events": events[-20:]}

    time.sleep(max(0.0, hold))
    for i, (px, py) in enumerate(points[1:], 1):
        # 每步都带到**同一个**目标窗口的客户区坐标（不重新钻取，
        # 拖动途中钻取会换来换去，反而把拖拽打断）
        if not emit(WM_MOUSEMOVE, mk, make_lparam(px, py), f"move-{i}"):
            break
        time.sleep(max(0.0, delay))

    time.sleep(max(0.0, hold))
    if release:
        last = points[-1]
        emit(up, 0, make_lparam(*last), "up")

    return {"ok": failed == 0 and sent > 1, "mode": "drag", "button": button,
            "from": list(points[0]), "to": list(points[-1]),
            "steps": len(points) - 1, "release": release,
            "sent": sent, "failed": failed, "events": events[-20:]}


def do_mouse(args, body: dict) -> dict:
    """
    鼠标滑动/拖拽/滚轮的统一入口（服务端和 CLI 共用）。

    body 里可以带：
      from / start        起点客户区坐标（默认客户区中心）
      dx / dy             相对滑动
      to                  绝对终点
      path                途经点
      pattern             手势
      steps               插值步数（默认 30）
      ease                平滑加减速
      duration            总时长秒 —— 给了就按它反推每步间隔（优先级高于 delay）
      delay               每步间隔秒（默认 0.02）
      button              拖拽时按哪个键（默认 left）；给了 button/hold 就走拖拽
      hold                按下与抬起前的额外停顿（秒）
      release             结束时是否抬起（默认 true）
      scroll              滚轮格数（正数向上，负数向下）
      axis                vertical / horizontal
      method              post / send / hardware / **sendinput**

    ★ 三种「动真格」的层次，按需要选：
      post/send   只发消息，光标不动 —— 最干净，但程序查 GetCursorPos 就失效
      hardware    SetCursorPos + mouse_event —— 真动光标，逐条发
      sendinput   ★ SendInput，系统输入队列的正规入口，可**整批原子提交**
                  （拖拽的按下/移动/抬起不会被用户真实鼠标插队拆散），
                  键盘侧还能只发扫描码。反作弊仍能识破注入（hDevice=NULL），
                  要「像真实硬件」只能上驱动级，不在本项目范围。
    """
    method = body.get("method", "post")
    if method not in ("post", "send", "hardware", "sendinput"):
        raise AppError("method 只能是 post / send / hardware / sendinput", code=2)

    hwnd = args.hwnd_int
    cw, ch = window_dimensions(hwnd, client_only=True)
    start_raw = body.get("from", body.get("start"))
    if start_raw is None:
        start = (cw // 2, ch // 2)
    else:
        if not isinstance(start_raw, (list, tuple)) or len(start_raw) != 2:
            raise AppError("from 必须是 [x, y]", code=2)
        try:
            start = (int(start_raw[0]), int(start_raw[1]))
        except (TypeError, ValueError):
            raise AppError(f"from 的坐标必须是整数，收到 {start_raw!r}", code=2)
    if not (0 <= start[0] < cw and 0 <= start[1] < ch):
        raise AppError(f"起点 {start} 落在客户区 {cw}x{ch} 之外", code=2)

    delay = float(body.get("delay", 0.02))
    if not (0 <= delay <= 60):
        raise AppError("delay 必须在 0~60 秒之间", code=2)

    # --- 滚轮 ---
    if body.get("scroll") is not None:
        axis = str(body.get("axis", "vertical")).lower()
        try:
            amount = int(body["scroll"])
        except (TypeError, ValueError):
            raise AppError("scroll 必须是整数", code=2)
        if method == "hardware":
            rec = mouse_scroll_hardware(hwnd, start[0], start[1], amount, axis=axis,
                                        restore_cursor=bool(body.get("restore_cursor", True)))
        elif method == "sendinput":
            rec = send_input_scroll(hwnd, start[0], start[1], amount, axis=axis,
                                    restore_cursor=bool(body.get("restore_cursor", True)))
        else:
            rec = mouse_scroll(hwnd, start[0], start[1], amount, axis=axis,
                               method=method, delay=max(delay, 0.01))
        rec.update({"from": list(start), "hwnd": hex(hwnd)})
        return rec

    # --- 滑动路径 ---
    pts, meta = _path_from_spec(hwnd, start, body)
    if not pts:
        raise AppError("路径是空的，没什么可滑", code=2)

    duration = body.get("duration")
    if duration is not None:
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            raise AppError("duration 必须是数字（秒）", code=2)
        if not (0 <= duration <= 3600):
            raise AppError("duration 必须在 0~3600 秒之间", code=2)
        delay = duration / max(1, len(pts))

    hold = float(body.get("hold", 0.03))
    if not (0 <= hold <= 60):
        raise AppError("hold 必须在 0~60 秒之间", code=2)

    button = body.get("button")
    is_drag = bool(button) or bool(body.get("drag"))

    if method == "sendinput":
        # SendInput：整批原子提交（拖拽最怕被用户真实鼠标插队拆散）
        rec = send_input_swipe(hwnd, [start] + pts,
                               button=(button or "left") if is_drag else None,
                               restore_cursor=bool(body.get("restore_cursor", True)),
                               batch=bool(body.get("batch", True)))
        rec["mode"] = "sendinput-drag" if is_drag else "sendinput-swipe"
    elif method == "hardware":
        # 真输入：真移动光标，所有程序都吃（代价是占用真实光标）
        rec = mouse_swipe_hardware(hwnd, [start] + pts,
                                   button=(button or "left") if is_drag else None,
                                   restore_cursor=bool(body.get("restore_cursor", True)),
                                   step_delay=delay)
        rec["mode"] = "hardware-drag" if is_drag else "hardware-swipe"
    elif is_drag:
        rec = mouse_drag(hwnd, [start] + pts, button=button or "left", method=method,
                         hold=hold, delay=delay, release=bool(body.get("release", True)),
                         deep=not bool(body.get("no_deep")))
        rec["mode"] = "drag"
    else:
        rec = mouse_swipe(hwnd, pts, method=method, delay=delay,
                          deep=not bool(body.get("no_deep")))

    input_mode = meta.pop("mode", None)
    rec.update(meta)
    rec["input_mode"] = input_mode       # delta / to / path / pattern
    rec["from"] = list(start)
    rec["to"] = list(pts[-1])
    rec["steps"] = len(pts)
    rec["ease"] = bool(body.get("ease"))
    rec["hwnd"] = hex(hwnd)
    return rec


def mouse_swipe_hardware(hwnd: int, points: list[tuple[int, int]], button: Optional[str] = None,
                         restore_cursor: bool = True, step_delay: float = 0.012) -> dict:
    """
    ★ 真输入版本：SetCursorPos + mouse_event 走真实的鼠标事件队列。

    为什么必须要有这个：
      合成的 WM_MOUSEMOVE 对**大量程序无效**，而且不是 bug、是设计 ——
      Windows 里鼠标位置是**全局状态**，程序随时可以调 GetCursorPos 查真实位置。
      很多现代程序（浏览器/Electron、游戏、自绘 UI）收到 WM_MOUSEMOVE 之后
      会去核对真实光标位置，发现没动就当这条消息不存在。
      点击为什么常常还行？因为「按下」是一个**事件**，程序认这条消息；
      而「移动」是**状态**，状态没法用消息伪造。

      所以：滑动/拖拽在合成消息无效的程序里，只有真输入这一条路。

    代价（必须说清楚）：
      * 会**真的移动用户的物理光标**，属于前台行为，会打扰正在操作的人；
      * 目标是「光标底下那个窗口」，不一定是原来那个 hwnd —— 所以调用方
        应该先把目标窗口调到前台（restore=False 时不自动做）。
      * restore_cursor=True 时结束时把光标放回原处（仅终点，不是全程）。

    button=None  → 纯移动（悬停滑动）
    button="left"/"right"/"middle" → 按下并保持，走完全程再抬起（真拖拽）
    """
    if button is not None and button not in HW_BUTTONS:
        raise AppError(f"真输入拖拽的 button 只能是 {list(HW_BUTTONS)}，收到 {button!r}", code=2)

    cw, ch = window_dimensions(hwnd, client_only=True)
    saved = wintypes.POINT()
    had_saved = bool(user32.GetCursorPos(ctypes.byref(saved)))

    events: list[dict] = []
    moved = 0
    failed = 0

    down_up = HW_BUTTONS.get(button) if button else None

    # 先落到起点，再按（按住拖拽时顺序不能反）
    first_sx, first_sy = resolve_screen_point(hwnd, points[0][0], points[0][1])
    if not user32.SetCursorPos(first_sx, first_sy):
        return {"ok": False, "mode": "hardware-drag" if button else "hardware-swipe",
                "error": ctypes.get_last_error() or ERROR_ACCESS_DENIED,
                "detail": f"SetCursorPos 到 ({first_sx},{first_sy}) 失败，取消以免误操作",
                "sent": 0, "failed": 1, "events": []}
    events.append({"action": "move", "screen_point": [first_sx, first_sy]})
    time.sleep(step_delay)

    if down_up:
        user32.mouse_event(down_up[0], 0, 0, 0, None)
        events.append({"action": "down", "button": button})
        time.sleep(0.03)

    for (px, py) in points[1:]:
        sx, sy = resolve_screen_point(hwnd, px, py)
        if not user32.SetCursorPos(sx, sy):
            failed += 1
            break
        # 真拖拽时移动消息由系统自己产生，带正确的按键状态位，不用我们伪造
        events.append({"action": "move", "point": [px, py], "screen_point": [sx, sy]})
        moved += 1
        time.sleep(step_delay)

    if down_up:
        time.sleep(0.03)
        user32.mouse_event(down_up[1], 0, 0, 0, None)
        events.append({"action": "up", "button": button})

    restored = False
    if restore_cursor and had_saved:
        time.sleep(0.03)
        restored = bool(user32.SetCursorPos(saved.x, saved.y))

    return {"ok": failed == 0 and (moved > 0 or len(points) == 1),
            "mode": "hardware-drag" if button else "hardware-swipe",
            "button": button, "from": list(points[0]), "to": list(points[-1]),
            "steps": len(points) - 1, "moved": moved, "failed": failed,
            "cursor_restored": restored, "events": events[-20:]}


def mouse_scroll_hardware(hwnd: int, x: int, y: int, amount: int, axis: str = "vertical",
                          restore_cursor: bool = True) -> dict:
    """
    ★ 真滚轮：把光标挪到目标位置上再发 mouse_event 的 WHEEL 事件。

    为什么要挪光标：系统的滚轮事件是发给**光标底下那个窗口**的，
      不挪过去就会滚错窗口 —— 这是真滚轮和 /mouse 消息投递最大的区别。

    axis="horizontal" 用 MOUSEEVENTF_HWHEEL（也不是所有程序支持横滚）。
    """
    MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
    flag = MOUSEEVENTF_WHEEL if axis == "vertical" else MOUSEEVENTF_HWHEEL

    saved = wintypes.POINT()
    had_saved = bool(user32.GetCursorPos(ctypes.byref(saved)))
    sx, sy = resolve_screen_point(hwnd, x, y)
    if not user32.SetCursorPos(sx, sy):
        return {"ok": False, "mode": "hardware-scroll",
                "error": ctypes.get_last_error() or ERROR_ACCESS_DENIED,
                "detail": f"SetCursorPos 到 ({sx},{sy}) 失败", "sent": 0, "failed": 1}
    time.sleep(0.05)

    sent = failed = 0
    for _ in range(abs(amount)):
        delta = WHEEL_DELTA if amount > 0 else -WHEEL_DELTA
        if axis == "horizontal":
            delta = -delta
        # mouse_event 的 dwData 是**有符号**的，负数要当 32 位传
        user32.mouse_event(flag, 0, 0, ctypes.c_ulong(delta & 0xFFFFFFFF).value, None)
        sent += 1
        time.sleep(0.03)

    restored = False
    if restore_cursor and had_saved:
        restored = bool(user32.SetCursorPos(saved.x, saved.y))

    return {"ok": failed == 0 and sent > 0, "mode": "hardware-scroll", "axis": axis,
            "amount": amount, "sent": sent, "failed": failed,
            "screen_point": [sx, sy], "cursor_restored": restored}


def mouse_swipe(hwnd: int, points: list[tuple[int, int]], method: str = "post",
                delay: float = 0.02, deep: bool = True) -> dict:
    """
    不按键的移动：鼠标从当前位置一路「划过」points（悬停滑动）。
    可以触发 hover 高亮、tooltip、画布上的 hover 手势。

    ★ 悬停滑动只发 WM_MOUSEMOVE，wParam=0（没有按键按下）。
      如果目标只认真的光标位置（比如游戏、DirectX），这条通路无效，
      那属于「合成消息天生无效」的情况，得用真移动。
    """
    events: list[dict] = []
    sent = failed = 0
    dest = hwnd
    if deep and points:
        dest, _, _, _ = deepest_child_at(hwnd, points[0][0], points[0][1])

    for i, (px, py) in enumerate(points):
        lp = make_lparam(px, py)
        if method == "send":
            out = ctypes.c_size_t(0)
            ctypes.set_last_error(0)
            res = user32.SendMessageTimeoutW(dest, WM_MOUSEMOVE, 0, lp,
                                             SMTO_ABORTIFHUNG | SMTO_BLOCK, 1000,
                                             ctypes.byref(out))
            err = ctypes.get_last_error()
            ok, err = (not (res == 0 and err)), (err if res == 0 else 0)
        else:
            ok, err = post_checked(dest, WM_MOUSEMOVE, 0, lp)
        events.append({"msg": hex(WM_MOUSEMOVE), "point": [px, py],
                       "wparam": "0x0", "lparam": hex(lp), "ok": ok, "error": err})
        if ok:
            sent += 1
        else:
            failed += 1
            break
        if i < len(points) - 1:
            time.sleep(max(0.0, delay))

    return {"ok": failed == 0 and sent > 0, "mode": "swipe", "sent": sent,
            "failed": failed, "events": events[-20:]}
