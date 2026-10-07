# -*- coding: utf-8 -*-
"""bgkit.bgserver.api.py —— HTTP 接口处理

从最初的单文件脚本拆出的一节。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

from bgkit import bgclick as bc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import argparse
import base64
import json
import os
import secrets
import sys
import threading
import time
import traceback
from .config import VERSION
from .focus import _find_focus_window
from .security import check_host_header, clamp_int, click_target, find_target, resolve_shot_path



# --------------------------------------------------------------------------
# HTTP 处理
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = f"bgserver/{VERSION}"
    protocol_version = "HTTP/1.1"

    # --- 基础工具 ---
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _authed(self) -> bool:
        if not check_host_header(self.headers.get("Host")):
            self._send(403, {"ok": False, "error": "Host 头不被接受（只允许本机访问）"})
            return False
        auth = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.token}"  # type: ignore[attr-defined]
        if not secrets.compare_digest(auth, expected):
            self._send(401, {"ok": False,
                             "error": "缺少或错误的 token。用 bgclient.py，"
                                      "或读 .bgstate/token.txt"})
            return False
        return True

    def _read_body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        if n > 8 * 1024 * 1024:
            raise bc.AppError("请求体过大（上限 8MB）", code=2)
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            raise bc.AppError("请求体不是合法 JSON", code=2)

    def log_message(self, fmt, *args):  # 静音默认日志，改用结构化输出
        if self.server.verbose:          # type: ignore[attr-defined]
            sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # --- 路由 ---
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/health":
            # health 不需要 token（方便探活），但也不泄露任何敏感信息
            try:
                uia_ok, uia_msg = bc.uia.uia_available()
            except Exception as e:      # noqa: BLE001 —— 探活绝不能因为 UIA 挂掉而失败
                uia_ok, uia_msg = False, f"{type(e).__name__}: {e}"
            self._send(200, {
                "ok": True, "service": "bgserver", "version": VERSION,
                "elevated": bc.is_admin(),
                "integrity": bc.integrity_name(bc.self_integrity()),
                "pid": os.getpid(), "uptime": round(time.time() - self.server.started, 1),
                "needs_token": True,
                "uia": {"available": uia_ok, "message": uia_msg},
            })
            return
        if not self._authed():
            return
        try:
            if u.path == "/windows":
                self._handle_windows(q)
            elif u.path == "/window":
                self._handle_window(q)
            elif u.path == "/uia":
                self._handle_uia_get(q)
            elif u.path == "/shutdown":
                self._send(200, {"ok": True, "message": "服务正在退出"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self._send(404, {"ok": False, "error": f"未知路径 {u.path}"})
        except bc.AppError as e:
            self._send(400, {"ok": False, "error": str(e), "code": e.code})
        except Exception as e:
            self._send(500, {"ok": False, "error": f"{type(e).__name__}: {e}",
                             "trace": traceback.format_exc()[-1500:]})

    def do_POST(self):
        if not self._authed():
            return
        u = urlparse(self.path)
        try:
            body = self._read_body()
            if u.path == "/click":
                self._handle_click(body)
            elif u.path == "/screenshot":
                self._handle_screenshot(body)
            elif u.path == "/probe":
                self._handle_probe(body)
            elif u.path == "/text":
                self._handle_text(body)
            elif u.path == "/key":
                self._handle_key(body)
            elif u.path == "/mouse":
                self._handle_mouse(body)
            elif u.path == "/uia":
                self._handle_uia(body)
            elif u.path == "/window":
                # 客户端用 POST 发目标信息（和 click/shot 保持一致），
                # 所以这里也要收 POST —— 只注册 GET 会让它 404。
                self._handle_window_post(body)
            elif u.path == "/shutdown":
                self._send(200, {"ok": True, "message": "服务正在退出"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self._send(404, {"ok": False, "error": f"未知路径 {u.path}"})
        except bc.AppError as e:
            self._send(400, {"ok": False, "error": str(e), "code": e.code})
        except Exception as e:
            self._send(500, {"ok": False, "error": f"{type(e).__name__}: {e}",
                             "trace": traceback.format_exc()[-1500:]})

    # --- 各接口 ---
    def _handle_windows(self, q: dict) -> None:
        rows = []
        for hwnd in bc.iter_top_level_windows():
            if not bc.user32.IsWindowVisible(hwnd):
                continue
            title = bc.window_title(hwnd)
            if not title:
                continue
            info = bc.describe(hwnd)
            info["postable"] = bc.probe_window(hwnd)["ok"]
            rows.append(info)
        rows.sort(key=lambda r: r["process"])
        self._send(200, {"ok": True, "count": len(rows), "windows": rows})

    def _handle_window(self, q: dict) -> None:
        hwnd_raw = (q.get("hwnd") or [None])[0]
        if not hwnd_raw:
            title = (q.get("title") or [None])[0]
            target = find_target({"title": title} if title else {})
        else:
            target = find_target({"hwnd": int(hwnd_raw, 0)})
        hwnd = target["hwnd"]
        pr = bc.probe_window(hwnd)
        target["postable"] = pr["ok"]
        if not pr["ok"]:
            target["blocked_reason"] = bc.describe_error(pr["error"])
        self._send(200, {"ok": True, "window": target})

    def _handle_window_post(self, body: dict) -> None:
        """POST /window —— 和 GET 版等价，只是目标信息从请求体来。"""
        target = find_target(body)
        hwnd = target["hwnd"]
        pr = bc.probe_window(hwnd)
        target["postable"] = pr["ok"]
        if not pr["ok"]:
            target["error"] = pr["error"]
            target["blocked_reason"] = bc.describe_error(pr["error"])
        target["self_integrity"] = bc.integrity_name(bc.self_integrity())
        target["target_integrity"] = bc.integrity_name(bc.process_integrity(target["pid"]))
        self._send(200, {"ok": True, "window": target})

    def _uia_query(self, body: dict, hwnd: int) -> dict:
        """UIA 查询的公共部分：解析参数、遍历、返回结果。

        为什么不用 FindAll 直接带条件去 UIA 里筛：一次遍历能同时服务
        「枚举」「按名找」「按类型找」三种需求，也避开了 CreatePropertyCondition
        的 VARIANT 入参。limit 内的元素量做 Python 侧筛选完全够快。
        """
        uiamod = bc.uia
        ok, why = uiamod.uia_available()
        if not ok:
            raise bc.AppError(f"UI Automation 不可用：{why}", code=2)

        max_depth = clamp_int(body.get("max_depth"), 1, 64, "max_depth", 12)
        limit = clamp_int(body.get("limit"), 1, 5000, "limit", 500)
        interactive_only = bool(body.get("interactive_only"))

        name = body.get("name")
        ctype = body.get("control_type")
        auto_id = body.get("automation_id")
        cls = body.get("class_name")
        exact = bool(body.get("name_exact"))
        for label, val in (("name", name), ("control_type", ctype),
                           ("automation_id", auto_id), ("class_name", cls)):
            if val is not None and not isinstance(val, str):
                raise bc.AppError(f"{label} 必须是字符串", code=2)
            if isinstance(val, str) and len(val) > 512:
                raise bc.AppError(f"{label} 过长（上限 512 字符）", code=2)

        if any((name, ctype, auto_id, cls)):
            res = uiamod.find(hwnd, name=name, control_type=ctype,
                              automation_id=auto_id, class_name=cls,
                              exact=exact, interactive_only=interactive_only,
                              max_depth=max_depth, limit=limit)
        else:
            res = uiamod.walk(hwnd, max_depth=max_depth, limit=limit,
                              interactive_only=interactive_only)

        payload = {
            "hwnd": hex(hwnd),
            "count": len(res.elements),
            "truncated": res.truncated,
            "limit": res.limit,
            "visited": res.visited,
            "max_depth": res.max_depth,
            "elapsed": res.elapsed,
            "failed_reason": res.failed_reason,
            "elements": [e.to_dict(hwnd) for e in res.elements],
        }
        return payload

    def _handle_uia(self, body: dict) -> None:
        """
        POST /uia —— UI Automation 元素查询。

        用系统自带的 UIA 读窗口里的元素树，按名字/控件类型/AutomationId 查元素，
        返回每个元素的**屏幕坐标和客户区坐标**。解决「坐标要靠截图数像素」这个
        老问题：先查元素拿坐标，再把坐标交给 /click 去点（那条路仍然不抢光标）。

        请求体：
            {"title": "记事本"}                              查什么窗口（照旧的目标字段）
            {"name": "确定", "control_type": "Button"}        查找条件（都可选，至少给一个才有意义）
            {"max_depth": 12, "limit": 500}                  遍历边界
            {"interactive_only": true}                       只要疑似可交互的元素

        不给任何查找条件时 = 遍历整棵树（等价于 --uia-walk）。

        ★ 关于 limit：超了就返回**部分结果**并置 truncated=true，不是报错。
          UIA 碰上几千行的列表会把目标程序卡住，所以必须有上限。

        ★ 关于坐标：elements[].rect 是 [left, top, right, bottom] 的**屏幕坐标**，
          client_center 已经换算成该窗口的**客户区坐标** —— 直接喂给 /click 的
          pos 字段即可。
        """
        target = find_target(body)
        payload = self._uia_query(body, target["hwnd"])
        payload.update({"title": target["title"], "process": target["process"]})
        self._send(200, {"ok": True, "uia": payload})

    def _handle_uia_get(self, q: dict) -> None:
        """GET /uia?title=xxx&name=yyy —— 和 POST 版等价，方便快速试。"""
        def one(key, default=None):
            v = q.get(key)
            return v[0] if v else default

        body: dict = {}
        for key in ("title", "process", "name", "control_type",
                    "automation_id", "class_name"):
            v = one(key)
            if v:
                body[key] = v
        if one("hwnd"):
            body["hwnd"] = int(one("hwnd"), 0)

        def as_int(key, default):
            v = one(key)
            if not v:
                return default
            try:
                return int(v)
            except ValueError:
                raise bc.AppError(f"{key} 必须是整数，收到 {v!r}", code=2)

        body["max_depth"] = as_int("max_depth", 12)
        body["limit"] = as_int("limit", 500)
        # 注意用 name_exact 而不是 exact：exact 在 find_target 里是「标题完全匹配」
        for flag in ("interactive_only", "name_exact"):
            v = one(flag)
            if v is not None:
                body[flag] = str(v).lower() in ("1", "true", "yes", "on")

        target = find_target(body)
        payload = self._uia_query(body, target["hwnd"])
        payload.update({"title": target["title"], "process": target["process"]})
        self._send(200, {"ok": True, "uia": payload})

    def _handle_click(self, body: dict) -> None:
        target = find_target(body)
        max_clicks = self.server.max_clicks   # type: ignore[attr-defined]
        result = click_target(target, body, max_clicks)
        self._send(200 if result["ok"] else 400, {"ok": result["ok"], "result": result})

    def _handle_screenshot(self, body: dict) -> None:
        target = find_target(body)
        fmt = (body.get("format") or "png").lower()
        if fmt not in ("png", "bmp"):
            raise bc.AppError("format 只能是 png 或 bmp", code=2)
        method = body.get("method", "auto")
        if method not in ("auto", "print", "bitblt"):
            raise bc.AppError("method 只能是 auto / print / bitblt", code=2)

        default_name = f"{target['process'].replace('.exe', '')}_{target['hwnd_hex']}.{fmt}"
        path = resolve_shot_path(body.get("path"), default_name)

        info = bc.save_screenshot(target["hwnd"], path,
                                  client_only=bool(body.get("client_only")),
                                  fmt=fmt, method=method)
        info.update({"hwnd": target["hwnd_hex"], "title": target["title"],
                     "process": target["process"]})

        if body.get("inline"):
            with open(info["path"], "rb") as f:
                info["base64"] = base64.b64encode(f.read()).decode("ascii")

        self._send(200, {"ok": True, "screenshot": info})

    def _handle_probe(self, body: dict) -> None:
        hwnd = body.get("hwnd")
        if isinstance(hwnd, str):
            hwnd = int(hwnd, 0)
        if hwnd is None:
            hwnd = find_target(body)["hwnd"]
        pr = bc.probe_window(hwnd)
        info = bc.describe(hwnd)
        info["postable"] = pr["ok"]
        if not pr["ok"]:
            info["error"] = pr["error"]
            info["blocked_reason"] = bc.describe_error(pr["error"])
        info["self_integrity"] = bc.integrity_name(bc.self_integrity())
        info["target_integrity"] = bc.integrity_name(bc.process_integrity(info["pid"]))
        self._send(200, {"ok": True, "probe": info})

    def _handle_text(self, body: dict) -> None:
        """
        向窗口发送文本（逐字符 WM_CHAR）。适合向输入框填内容。

        ★ 关于「发给谁」：
          GetFocus() 只返回**调用线程**的焦点窗口 —— 服务线程不是目标窗口的线程，
          所以 GetFocus() 几乎永远是 NULL。用 GetGUIThreadInfo 拿目标线程的焦点，
          跨进程也能拿到正确的子控件。
          拿不到时才退回顶层窗口（对记事本这类直接收 WM_CHAR 的程序够用）。
        """
        target = find_target(body)
        text = body.get("text")
        if not isinstance(text, str):
            raise bc.AppError("text 必须是字符串", code=2)
        if len(text) > 4096:
            raise bc.AppError("text 过长（上限 4096 字符）", code=2)

        hwnd = target["hwnd"]
        dest = _find_focus_window(hwnd) or hwnd

        sent = 0
        for ch in text:
            ok, err = bc.post_checked(dest, 0x0102, ord(ch), 0)   # WM_CHAR
            if not ok:
                self._send(400, {"ok": False, "sent": sent,
                                 "error": bc.describe_error(err), "error_code": err,
                                 "target": hex(dest)})
                return
            sent += 1
            time.sleep(0.005)
        if body.get("enter"):
            bc.post_checked(dest, 0x0100, 0x0D, 0)   # WM_KEYDOWN VK_RETURN
            bc.post_checked(dest, 0x0101, 0x0D, 0)   # WM_KEYUP
        self._send(200, {"ok": True, "sent": sent, "target": hex(dest)})

    def _handle_key(self, body: dict) -> None:
        """
        向窗口发送键盘输入：单键 / 组合键 / 多键同按。走的还是消息投递，不抢焦点。

        ★ 三种模式，按 body 里给了哪个字段决定（互斥，一次只能用一种）：

          1. chord / chords —— 组合键。修饰键的按下与抬起顺序由库自动配好：
                 "chord": "ctrl+shift+s"          # 一次
                 "chords": ["ctrl+a", "ctrl+c"]   # 依次
               和 /text 的区别：这是真正的 WM_KEYDOWN 通路，菜单快捷键、
               IDE、游戏、画布控件都吃；/text 的纯 WM_CHAR 它们一律当没看见。

          2. keys（+ hold）—— 真正的「多键同按」。列表按原顺序按下：
                 "keys": ["ctrl", "shift", "a"]
                 "hold_seconds": 1.5              # 按住不放 1.5 秒再松开
               不给 hold_seconds 就是一次普通的组合键（等于模式 1）。
               松开顺序自动倒过来（先放主键，再放修饰键），和真人一致。

          3. text —— 逐字符输入。默认 auto：每个字符**只走一条通道** ——
                 需要 Shift/Ctrl/Alt 的走 WM_KEYDOWN/UP，不需要的直接发 WM_CHAR。
                 当前键盘布局打不出的字符（中文、emoji）只能走 WM_CHAR。
                 ★ 可选 "mode": "keys"（全走按键消息，给 IDE/游戏/画布）
                   或 "mode": "both"（两条通道都发 —— 老行为，在记事本这类
                   同时认两条通道的控件里字符会翻倍，只在目标确实不认某一条时用）。

        ★ 发给谁：和 /text 一样，优先发给目标窗口所在线程的焦点控件
          （GetGUIThreadInfo），拿不到才退回顶层窗口。键盘消息必须命中焦点控件，
          否则程序收到也当没收到。

        ★ method 支持 post / send / **sendinput**：
          post/send   把键盘消息投给目标窗口（后台，不动焦点，但有些程序不认）；
          sendinput   ★ SendInput 走系统输入队列，默认**只发扫描码**
                      （KEYEVENTF_SCANCODE）—— 只读底层键盘输入的程序
                      （DirectInput / Raw Input 类）对虚拟键码不买账，
                      对扫描码买账。代价：会打进**当前前台窗口**，
                      所以会先把目标窗口调到前台，属于前台行为。
                      注意 Raw Input 程序仍能通过 hDevice=NULL 识别出这是注入，
                      要「像真实硬件」只能上驱动级，不在本服务范围内。
        """
        target = find_target(body)
        hwnd = target["hwnd"]

        method = body.get("method", "post")
        if method not in ("post", "send", "sendinput"):
            raise bc.AppError("method 只能是 post / send / sendinput", code=2)

        if method == "sendinput":
            # 真键盘是全局的：先把目标调到前台，否则按键会打进别人窗口
            bc.user32.SetForegroundWindow(hwnd)
            time.sleep(0.15)

        dest = hwnd if method == "sendinput" else (_find_focus_window(hwnd) or hwnd)
        repeat = clamp_int(body.get("repeat"), 1, 1000, "repeat", 1)
        interval = float(body.get("interval", 0.06))
        hold_gap = float(body.get("hold_gap", 0.02))
        for name, val in (("interval", interval), ("hold_gap", hold_gap)):
            if not (0 <= val <= 60):
                raise bc.AppError(f"{name} 必须在 0~60 秒之间", code=2)
        with_char = body.get("char")
        if with_char is not None and not isinstance(with_char, bool):
            raise bc.AppError("char 只能是 true / false", code=2)

        # --- 收集组合键写法 ---
        specs: list[str] = []
        if body.get("chord") is not None:
            if not isinstance(body["chord"], str):
                raise bc.AppError("chord 必须是字符串，如 \"ctrl+shift+s\"", code=2)
            specs.append(body["chord"])
        if body.get("chords") is not None:
            raw = body["chords"]
            if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
                raise bc.AppError("chords 必须是字符串数组，如 [\"ctrl+a\", \"ctrl+c\"]", code=2)
            specs.extend(raw)
        if len(specs) > 64:
            raise bc.AppError("一次最多 64 个组合键", code=2)
        for s in specs:
            if len(s) > 64:
                raise bc.AppError(f"组合键写得太长：{s[:32]}…", code=2)

        keys = body.get("keys")
        text = body.get("text")
        if keys is not None and not isinstance(keys, list):
            raise bc.AppError("keys 必须是字符串数组，如 [\"ctrl\", \"shift\", \"a\"]", code=2)
        if text is not None and not isinstance(text, str):
            raise bc.AppError("text 必须是字符串", code=2)

        modes = [bool(specs), keys is not None, text is not None]
        if sum(modes) > 1:
            raise bc.AppError("chord/chords、keys、text 只能选一种", code=2)
        if not any(modes):
            raise bc.AppError(
                "得给一种键盘输入：chord（\"ctrl+s\"）/ chords（数组）/ keys（数组）/ text", code=2)

        started = time.time()

        # --- 模式 1：组合键 ---
        if specs:
            chords = [bc.parse_chord(s) for s in specs]
            if method == "sendinput":
                result = bc.send_input_chords(chords, repeat=repeat, interval=interval,
                                              use_scancode=bool(body.get("scancode", True)))
            else:
                result = bc.send_key_sequence(dest, chords, method=method, repeat=repeat,
                                              interval=interval, hold=hold_gap,
                                              with_char=with_char)
            result["mode"] = "chord"
            result["specs"] = specs

        # --- 模式 2：按键列表（可按住） ---
        elif keys is not None:
            vks = bc.parse_key_list(keys)
            if len(vks) > 32:
                raise bc.AppError("keys 一次最多 32 个键", code=2)
            if method == "sendinput":
                result = bc.send_input_keys_hold(
                    vks, hold_seconds=float(body.get("hold_seconds", 0.2)),
                    repeat=clamp_int(repeat, 1, 100, "repeat", 1),
                    interval=interval, use_scancode=bool(body.get("scancode", True)))
            elif body.get("hold") or body.get("hold_seconds") is not None:
                hold_seconds = float(body.get("hold_seconds", 0.5))
                if not (0 <= hold_seconds <= 3600):
                    raise bc.AppError("hold_seconds 必须在 0~3600 秒之间", code=2)
                repeat = clamp_int(repeat, 1, 100, "repeat", 1)
                sent = failed = runs = 0
                events: list[dict] = []
                for _ in range(repeat):
                    down = bc.send_keys_state(dest, vks, False, method,
                                              include_char=bool(with_char))
                    sent += down["sent"]
                    failed += down["failed"]
                    events.extend(down["events"])
                    if down["failed"]:
                        break
                    time.sleep(hold_seconds)
                    up = bc.send_keys_state(dest, list(reversed(vks)), True, method)
                    sent += up["sent"]
                    failed += up["failed"]
                    events.extend(up["events"])
                    runs += 1
                    if up["failed"]:
                        break
                    if repeat > 1:
                        time.sleep(max(0.0, interval))
                result = {"ok": failed == 0 and sent > 0, "sent": sent, "failed": failed,
                          "runs": runs, "held_seconds": hold_seconds,
                          "keys": [hex(v) for v in vks], "with_char": bool(with_char),
                          "events": events[-40:]}
            else:
                result = bc.send_key_sequence(dest, [vks], method=method, repeat=repeat,
                                              interval=interval, hold=hold_gap,
                                              with_char=with_char)
                result["runs"] = result.get("runs", repeat)
            result["mode"] = "keys"
            result["specs"] = list(keys)

        # --- 模式 3：逐字符输入 ---
        else:
            if len(text) > 4096:
                raise bc.AppError("text 过长（上限 4096 字符）", code=2)
            if method == "sendinput":
                result = bc.send_input_text(text, interval=max(0.005, interval),
                                            use_scancode=bool(body.get("scancode", True)))
            else:
                mode = str(body.get("mode", "auto")).lower()
                if mode not in ("auto", "keys", "both"):
                    raise bc.AppError("mode 只能是 auto / keys / both", code=2)
                result = bc.send_text_as_keys(dest, text, method=method, mode=mode,
                                              hold=hold_gap, interval=max(0.005, interval))
            result["mode"] = "text"
            result["specs"] = [text[:64]]

        result["elapsed"] = round(time.time() - started, 3)
        result["hwnd"] = target["hwnd_hex"]
        result["title"] = target["title"]
        result["process"] = target["process"]
        # 和 /click 一样：投递没成功就该让调用方看到失败（退出码 1）
        self._send(200 if result.get("ok") else 400,
                   {"ok": bool(result.get("ok")), "key": result, "target": hex(dest)})

    def _handle_mouse(self, body: dict) -> None:
        """
        模拟鼠标滑动 / 拖拽 / 滚轮。和点击一样走消息投递，不动真实光标、不抢焦点。

        五种玩法（一次只用一种）：

          1. 相对滑动（最常用）      {"dx": 0, "dy": -500}        向上滑 500 像素
          2. 滑到绝对坐标            {"to": [400, 300]}
          3. 途经点轨迹              {"path": [[100,100],[300,300],[500,200]]}
          4. 手势                    {"pattern": "circle", "distance": 200}
                                     pattern: line/up/down/left/right/circle/square/zigzag
          5. 滚轮                    {"scroll": -5}               向下滚 5 格
                                     {"scroll": 3, "axis": "horizontal"}  向右滚

        拖动类（滑块、列表内容、地图、画布）加 "button" 就变成按住拖动：
            {"from": [200, 400], "dy": -300, "button": "left"}

        ★ 滑动不是「发一条消息」：
          拖动交互靠**连续的 WM_MOUSEMOVE** 累积，所以默认按 steps=30 插值走过去，
          每一步都规范地带上按键状态位（MK_LBUTTON 等）—— 不带的话程序认为
          中途已经松手，拖动会断在起点。delay 控制每步间隔，duration 可以直接
          指定整段时长（给「要拖 1 秒」这种需求用）。

        ★ 坐标是客户区坐标；滚轮的 lParam 按 Windows 规定用屏幕坐标，库内部换算。

        ★ 只允许 post / send / hardware：
          post/send 走消息投递，不动真实光标；
          **hardware 是真输入**（SetCursorPos + mouse_event），会占用用户的真实光标，
          但「合成消息无效」的程序（浏览器/Electron、游戏、自绘 UI）只有这一条路 ——
          因为鼠标位置是全局状态，程序会查真实光标，消息伪造不了状态。
        """
        target = find_target(body)
        args = argparse.Namespace(hwnd_int=target["hwnd"])
        started = time.time()
        result = bc.do_mouse(args, body)
        result["elapsed"] = round(time.time() - started, 3)
        result["hwnd"] = target["hwnd_hex"]
        result["title"] = target["title"]
        result["process"] = target["process"]
        self._send(200 if result.get("ok") else 400,
                   {"ok": bool(result.get("ok")), "mouse": result,
                    "target": result.get("hwnd")})
