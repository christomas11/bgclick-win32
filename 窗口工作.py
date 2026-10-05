#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bgserver.py —— 后台点击/截图的常驻本地服务（供 agent skill 调用）

把 bgclick 的能力包成一个**只监听本机**的 HTTP 小服务，常驻后台跑。
首个请求或启动时自动申请管理员权限（弹一次 UAC），之后就一直以管理员身份待命，
不用每次点击都弹窗。

快速开始
--------
  # 启动（会弹 UAC，之后常驻；默认带托盘图标）
  python bgserver.py

  # 查状态
  python bgclient.py health

  # 列出窗口
  python bgclient.py windows

  # 点一下
  python bgclient.py click --title "记事本" --pos 400,300

  # 截图
  python bgclient.py shot --title "记事本" --out shots/note.png

  # 键盘：组合键 / 多键同按 / 逐字符真按键（走 WM_KEYDOWN，不是 WM_CHAR）
  python bgclient.py key --title "记事本" --keys ctrl+shift+s
  python bgclient.py key --title "记事本" --hold ctrl+shift+a --hold-seconds 1.5
  python bgclient.py key --title "记事本" --type "Hello!"

  # 停止
  python bgclient.py shutdown

─────────────── 安全设计（请先读这段）───────────────
这个服务跑在管理员权限下、还能模拟鼠标键盘。所以它是一块**提权面**，
下面每条限制都是刻意的，不是多余：

1. **只绑 127.0.0.1**。绝不监听 0.0.0.0。外网/局域网连不上。
2. **强制 Bearer token**。启动时随机生成，存到 .bgstate/token.txt。
   没有 token 一律 401 —— 防止本机其他用户/其他程序顺手驱动它。
3. **校验 Host 头**，只接受 localhost / 127.0.0.1 / [::1]。
   防 DNS rebinding：否则恶意网页能把你的浏览器变成攻击跳板。
4. **截图落盘路径被限制**在允许的根目录内（默认 ./shots 和 ./.bgstate），
   防止被用来覆盖任意文件。
5. **点击次数有上限**（默认单次请求最多 10000 次），
   防止一个错误请求把目标程序点爆。
6. **不提供任意命令执行**。只有点击/截图/文本这几个动作，没有 shell。
7. token 文件权限设为仅当前用户可读。

如果这些限制挡了你的正当用法，用命令行参数放开（--max-clicks 等），
不要去改代码里的检查。

本文件相对最初版本的修复（1.0.1）：
  1. 顶部 import subprocess（do_restart 用到）。
  2. state_dir() 与 bgclient 完全对齐。
  3. 托盘默认开启（不再依赖 sys.frozen）。
  4. do_restart 给新进程带 --_wait-for-port-release，
     避免新进程在老进程端口还没释放时误判「已有实例」而直接退出。
  5. 托盘初始化失败不再静默退回无托盘模式：写日志 + 弹 MessageBox。

1.1.0 新增：
  6. /key 接口 —— 键盘输入。单键、组合键（ctrl+shift+s）、多键同按（按住不放）、
     逐字符真按键。走 WM_KEYDOWN/WM_KEYUP + 规范 lParam，不是原来只有的 WM_CHAR。
     原来的 /text 保留不动（纯字符通道，适合中英文文本）。
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import urlparse, parse_qs

# 复用 bgclick 里的 Win32 实现，避免两份代码漂移
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bgclick as bc  # noqa: E402

VERSION = "1.1.0"
DEFAULT_PORT = 8765

# 允许截图落盘的根目录（启动时填充为绝对路径）
ALLOWED_SHOT_ROOTS: list[str] = []


# --------------------------------------------------------------------------
# 状态目录 / token
# --------------------------------------------------------------------------


def state_dir() -> str:
    """
    状态目录（token / 端口 / pid）。

    必须和 bgclient.py 里的 state_dir() **完全一致**（同一套回退链），
    否则客户端和服务会读到不同的 token。

    回退顺序：
      1. 环境变量 BGCLICK_STATE_DIR（显式指定，最优先）
      2. ~/.bgclick                     （正常情况，用 expanduser("~")，
                                          不用 USERPROFILE，避免与 client 分叉）
      3. 临时目录 /bgclick-state        （~ 不可写时，比如受限沙箱）
    """
    candidates = []
    env = os.environ.get("BGCLICK_STATE_DIR")
    if env:
        candidates.append(os.path.abspath(env))
    try:
        home = os.path.expanduser("~")
        if home and home != "~":
            candidates.append(os.path.join(home, ".bgclick"))
    except Exception:
        pass
    try:
        candidates.append(os.path.join(tempfile.gettempdir(), "bgclick-state"))
    except Exception:
        pass

    last_err: Optional[Exception] = None
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write-test")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            return d
        except Exception as e:
            last_err = e
            continue

    raise PermissionError(
        "找不到可写的状态目录。试过：\n  " + "\n  ".join(candidates)
        + f"\n最后一个错误：{last_err}")


def token_path() -> str:
    return os.path.join(state_dir(), "token.txt")


def port_path() -> str:
    return os.path.join(state_dir(), "port.txt")


def pid_path() -> str:
    return os.path.join(state_dir(), "server.pid")


def load_or_create_token(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    p = token_path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                t = f.read().strip()
            if t:
                return t
        except Exception:
            pass
    t = secrets.token_urlsafe(32)
    with open(p, "w", encoding="utf-8") as f:
        f.write(t)
    try:
        os.chmod(p, 0o600)  # 仅当前用户可读写
    except Exception:
        pass
    return t


# --------------------------------------------------------------------------
# 参数校验与安全助手
# --------------------------------------------------------------------------


def check_host_header(host: Optional[str]) -> bool:
    """
    只接受本机 Host。防 DNS rebinding：
    恶意网页可以把自己解析到 127.0.0.1 来访问本地服务，
    但它发来的 Host 头会是自己的域名，被这里挡住。
    """
    if not host:
        return False
    h = host.strip().lower()
    if h.startswith("["):                     # IPv6 形如 [::1]:8765
        h = h[1:].split("]")[0]
    else:
        h = h.split(":")[0]
    return h in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def resolve_shot_path(user_path: Optional[str], default_name: str) -> str:
    """
    把用户给的截图路径解析成绝对路径，并确保落在允许的根目录内。
    越界直接拒绝 —— 防止这个提权服务被用来覆盖任意文件。
    """
    if not user_path:
        target = os.path.join(ALLOWED_SHOT_ROOTS[0], default_name)
    else:
        target = user_path
        if not os.path.isabs(target):
            target = os.path.join(ALLOWED_SHOT_ROOTS[0], target)
    target = os.path.abspath(target)

    for root in ALLOWED_SHOT_ROOTS:
        root_abs = os.path.abspath(root)
        try:
            if os.path.commonpath([target, root_abs]) == root_abs:
                return target
        except ValueError:
            continue  # 不同盘符，肯定不在这个根下面
    raise bc.AppError(
        f"截图路径越界：{target}\n只允许写到这些目录下：\n  "
        + "\n  ".join(ALLOWED_SHOT_ROOTS), code=2)


def clamp_int(value: Any, lo: int, hi: int, name: str, default: int) -> int:
    if value is None:
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise bc.AppError(f"{name} 必须是整数，收到 {value!r}", code=2)
    if n < lo or n > hi:
        raise bc.AppError(f"{name} 超出允许范围 [{lo}, {hi}]，收到 {n}", code=2)
    return n


def find_target(body: dict) -> dict:
    """从请求体里定位目标窗口，返回 describe() 结果。"""
    args = argparse.Namespace(
        hwnd=body.get("hwnd"),
        title=body.get("title"),
        regex=bool(body.get("regex")),
        exact=bool(body.get("exact")),
        process=body.get("process"),
        only_visible=not bool(body.get("include_hidden")),
        index=int(body.get("index", 0)),
    )
    if isinstance(args.hwnd, str):
        args.hwnd = int(args.hwnd, 0)
    cands = bc.match_windows(args)
    if not cands:
        raise bc.AppError("没找到匹配的窗口", code=1)
    if args.index >= len(cands) or args.index < 0:
        raise bc.AppError(f"--index {args.index} 越界（匹配到 {len(cands)} 个）", code=2)
    return cands[args.index]


def click_target(target: dict, body: dict, max_clicks: int) -> dict:
    """对已解析的目标窗口执行点击。"""
    hwnd = target["hwnd"]
    cw, ch = target["client_size"]

    count = clamp_int(body.get("count"), 0, max_clicks, "count", 1)
    interval = float(body.get("interval", 0.5))
    if not (0 <= interval <= 3600):
        raise bc.AppError("interval 必须在 0~3600 秒之间", code=2)
    jitter = clamp_int(body.get("jitter"), 0, 500, "jitter", 0)

    button = body.get("button", "left")
    if button not in bc.BUTTONS:
        raise bc.AppError(f"button 只能是 {list(bc.BUTTONS)}，收到 {button!r}", code=2)
    method = body.get("method", "post")
    if method not in ("post", "send", "hardware"):
        raise bc.AppError("method 只能是 post / send / hardware", code=2)

    # 坐标
    if body.get("center"):
        base = (cw // 2, ch // 2)
    elif body.get("pos"):
        base = (int(body["pos"][0]), int(body["pos"][1]))
    elif body.get("screen_pos"):
        base = bc.resolve_client_point(hwnd, int(body["screen_pos"][0]),
                                       int(body["screen_pos"][1]))
    else:
        raise bc.AppError("必须给 center / pos / screen_pos 之一", code=2)

    if not (0 <= base[0] < cw and 0 <= base[1] < ch):
        raise bc.AppError(f"坐标 {base} 落在客户区 {cw}x{ch} 之外", code=2)

    args = argparse.Namespace(
        button=button, method=method, deep=not bool(body.get("no_deep")),
        restore_cursor=bool(body.get("restore_cursor", True)),
    )

    if body.get("activate") and method != "hardware":
        bc.user32.SetForegroundWindow(hwnd)
        time.sleep(0.15)

    results = []
    done = 0
    failed = 0
    while count == 0 or done < count:
        if not bc.user32.IsWindow(hwnd):
            break
        x, y = base
        if jitter:
            import random
            x += random.randint(-jitter, jitter)
            y += random.randint(-jitter, jitter)
        x, y = max(0, min(x, cw - 1)), max(0, min(y, ch - 1))

        if args.deep and method != "hardware":
            rec = bc.resolving_click(hwnd, x, y, args)
        else:
            rec = bc.do_click(hwnd, x, y, args)
        rec["index"] = done
        results.append(rec)
        if not rec["ok"]:
            failed += 1
            if not body.get("force_continue"):
                break
        done += 1
        if count != 0 and done >= count:
            break
        if count == 0 and done >= max_clicks:   # 无限循环也要有个天花板
            break
        time.sleep(max(0.0, interval))

    return {
        "hwnd": target["hwnd_hex"], "title": target["title"],
        "process": target["process"], "base_pos": list(base),
        "clicked": done, "failed": failed,
        "ok": failed == 0 and done > 0,
        "results": results[-10:],   # 只回最近 10 条，别把响应撑爆
    }


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
            self._send(200, {
                "ok": True, "service": "bgserver", "version": VERSION,
                "elevated": bc.is_admin(),
                "integrity": bc.integrity_name(bc.self_integrity()),
                "pid": os.getpid(), "uptime": round(time.time() - self.server.started, 1),
                "needs_token": True,
            })
            return
        if not self._authed():
            return
        try:
            if u.path == "/windows":
                self._handle_windows(q)
            elif u.path == "/window":
                self._handle_window(q)
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

          3. text —— 逐字符「真按键」输入，每个字符拆成 修饰键+主键 再补 WM_CHAR。
                 当前键盘布局打不出的字符（中文、emoji）自动退回纯 WM_CHAR。

        ★ 发给谁：和 /text 一样，优先发给目标窗口所在线程的焦点控件
          （GetGUIThreadInfo），拿不到才退回顶层窗口。键盘消息必须命中焦点控件，
          否则程序收到也当没收到。

        ★ 只支持 post / send 两种投递方式。真键盘（SendInput）是全局的，
          会打进用户正在用的窗口，本接口不提供 —— 需要就用 bgclick.py --method hardware。
        """
        target = find_target(body)
        hwnd = target["hwnd"]
        dest = _find_focus_window(hwnd) or hwnd

        method = body.get("method", "post")
        if method not in ("post", "send"):
            raise bc.AppError("method 只能是 post / send", code=2)
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
            if body.get("hold") or body.get("hold_seconds") is not None:
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

        # --- 模式 3：逐字符真按键 ---
        else:
            if len(text) > 4096:
                raise bc.AppError("text 过长（上限 4096 字符）", code=2)
            result = bc.send_text_as_keys(dest, text, method=method,
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


# --------------------------------------------------------------------------
# 跨进程拿目标窗口的焦点控件
# --------------------------------------------------------------------------


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", bc.wintypes.DWORD),
        ("flags", bc.wintypes.DWORD),
        ("hwndActive", bc.wintypes.HWND),
        ("hwndFocus", bc.wintypes.HWND),
        ("hwndCapture", bc.wintypes.HWND),
        ("hwndMenuOwner", bc.wintypes.HWND),
        ("hwndMoveSize", bc.wintypes.HWND),
        ("hwndCaret", bc.wintypes.HWND),
        ("rcCaret", bc.wintypes.RECT),
    ]


bc.user32.GetGUIThreadInfo.argtypes = [bc.wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
bc.user32.GetGUIThreadInfo.restype = bc.wintypes.BOOL


def _find_focus_window(hwnd: int) -> Optional[int]:
    """
    拿目标窗口所属线程的焦点子窗口。

    优先级：目标线程的 hwndFocus > 目标线程的 hwndActive > 本线程 GetFocus()
    都拿不到返回 None（调用方退回顶层窗口）。
    """
    # 先试目标窗口所在线程的 GUI 状态
    try:
        tid = bc.user32.GetWindowThreadProcessId(hwnd, None)
        if tid:
            gti = GUITHREADINFO()
            gti.cbSize = ctypes.sizeof(GUITHREADINFO)
            if bc.user32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
                if gti.hwndFocus and bc.user32.IsWindow(gti.hwndFocus):
                    return int(gti.hwndFocus)
                if gti.hwndActive and bc.user32.IsWindow(gti.hwndActive):
                    return int(gti.hwndActive)
    except Exception:
        pass

    # 兜底：本线程的 GetFocus（同进程窗口才有意义）
    try:
        f = bc.user32.GetFocus()
        if f and bc.user32.IsWindow(f):
            return int(f)
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------
# 启动
# --------------------------------------------------------------------------


def elevation_argv(argv: list[str]) -> list[str]:
    """提权后要用的参数（去掉提权标记，保留其它）。"""
    out = [a for a in argv if a not in ("--elevate", "--_elevated")]
    return out + ["--_elevated"]


def relaunch_elevated(argv: list[str]) -> int:
    """
    提权重启。返回 0 = 已成功拉起；非 0 = 没成，调用方应回退到不提权继续。

    ★ 实测坑：在沙箱/受限环境里调 ShellExecuteW("runas") 会**返回成功**
      （返回值 > 32），但实际上什么都没启动（没有 UAC 可弹）。
      所以这里返回 0 也不代表对面真的起来了 —— 调用方必须自己探活确认。
    """
    exe = sys.executable
    base = os.path.basename(exe).lower()
    if base.startswith("pythonw"):
        cand = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(cand):
            exe = cand
    if getattr(sys, "frozen", False):
        # ★ 打包成 exe 后 exe 自己就是脚本，不能再把 __file__ 当脚本路径传进去。
        #   （PyInstaller 下 __file__ 是 exe 自己的路径，甚至可能是空串 / 构建机路径）
        #   之前无条件拼 [script] + argv，会让子进程收到一个多余的位置参数，
        #   argparse 直接报 "unrecognized arguments" 并以退出码 2 结束 ——
        #   而 console=False 的 exe 看不到任何输出，表现就是「UAC 点完就闪退」。
        params = subprocess.list2cmdline(elevation_argv(argv))
    else:
        script = os.path.abspath(__file__)
        params = subprocess.list2cmdline([script] + elevation_argv(argv))
    print("正在申请管理员权限…… 屏幕上会弹 UAC，请点「是」。")
    try:
        rc = int(bc.shell32.ShellExecuteW(None, "runas", exe, params, os.getcwd(), 1))
    except Exception as e:
        print(f"提权调用异常：{e}", file=sys.stderr)
        return 5
    if rc <= 32:
        print(f"提权失败（ShellExecuteW 返回 {rc}），改为不提权启动。", file=sys.stderr)
        return 5
    print("已请求以管理员权限启动。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bgserver.py",
        description="后台点击/截图的常驻本地服务（仅监听 127.0.0.1）",
    )
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"端口（默认 {DEFAULT_PORT}）")
    p.add_argument("--token", help="固定 token（默认自动生成并存在 .bgstate/token.txt）")
    p.add_argument("--elevate", action="store_true", help="启动时申请管理员权限（默认自动）")
    p.add_argument("--no-elevate", dest="auto_elevate", action="store_false", default=True,
                   help="不提权，以当前权限运行（很多窗口会点不动）")
    p.add_argument("--_elevated", action="store_true", help=argparse.SUPPRESS)
    # 重启场景：新进程带这个标记时会先等端口释放，避免误判「已有实例」
    p.add_argument("--_wait-for-port-release", action="store_true",
                   help=argparse.SUPPRESS)
    p.add_argument("--shot-dir", default="shots", help="允许截图落盘的目录（默认 ./shots）")
    p.add_argument("--extra-shot-dir", action="append", default=[],
                   help="额外的允许截图目录，可重复")
    p.add_argument("--max-clicks", type=int, default=10000,
                   help="单次请求允许的最大点击次数（默认 10000）")
    p.add_argument("--verbose", action="store_true", help="打印每个请求日志")

    # ★ 托盘默认开启：`--tray` 保留为兼容参数（显式给也不报错），
    #   真正控制开关的是 `--no-tray`。argparse 里同一个 dest 被两个参数操作时，
    #   最后定义的那个参数的 default 生效 —— 所以 --no-tray 显式写 default=True，
    #   --tray 用 default=argparse.SUPPRESS 避免覆盖它。
    p.add_argument("--tray", action="store_true", default=argparse.SUPPRESS,
                   help="显示系统托盘图标（默认就开；给不给都一样）")
    p.add_argument("--no-tray", dest="tray", action="store_false", default=True,
                   help="不显示托盘图标（纯后台跑，适合 CI / 无桌面环境）")

    p.add_argument("--no-console", action="store_true",
                   help="抑制 stdout 输出（无窗口 exe 下只能靠日志文件）")
    p.add_argument("--open-log", action="store_true", help="启动时打开日志窗口")
    return p


# --------------------------------------------------------------------------
# 日志（无窗口 exe 下看不到 stdout，所以必须落盘）
# --------------------------------------------------------------------------


class Logger:
    """
    同时写日志文件和（可用时）stdout。

    打包成 --noconsole 的 exe 后 stdout 是无效句柄，往里写会直接抛异常，
    所以这里要能优雅降级到只写文件。
    """

    def __init__(self, path: str, also_stdout: bool = True):
        self.path = path
        self.also_stdout = also_stdout and self._stdout_ok()
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)

    @staticmethod
    def _stdout_ok() -> bool:
        try:
            if sys.stdout is None:
                return False
            sys.stdout.write("")
            sys.stdout.flush()
            return True
        except Exception:
            return False

    def write(self, text: str = "") -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}"
        with self._lock:
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass
            if self.also_stdout:
                try:
                    print(text, flush=True)
                except Exception:
                    self.also_stdout = False


# --------------------------------------------------------------------------
# 单实例锁
# --------------------------------------------------------------------------


class SingleInstance:
    """
    用命名互斥体保证同一端口只有一个服务实例。

    没有这个的话，用户双击 exe 两次就会出现两个服务抢同一个端口，
    第二个静默失败，用户体验很差（而且托盘出现两个图标）。
    """

    def __init__(self, name: str):
        self.name = name
        self.handle = None

    def acquire(self) -> bool:
        ERROR_ALREADY_EXISTS = 183
        # 用 bc 里已经加载好的 kernel32 / wintypes，别再重复 LoadLibrary
        k32 = bc.kernel32
        wt = bc.wintypes
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
        k32.CreateMutexW.restype = wt.HANDLE
        ctypes.set_last_error(0)
        self.handle = k32.CreateMutexW(None, False, f"Global\\{self.name}")
        err = ctypes.get_last_error()
        if not self.handle:
            return True          # 拿不到互斥体就当没锁，别把服务挡在门外
        return err != ERROR_ALREADY_EXISTS

    def release(self) -> None:
        if self.handle:
            try:
                bc.kernel32.CloseHandle(self.handle)
            except Exception:
                pass
            self.handle = None


def find_existing(port: int) -> Optional[int]:
    """端口上已有服务在跑就返回它的 pid，否则 None。"""
    try:
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return json.loads(resp.read().decode("utf-8")).get("pid")
    except Exception:
        return None


def fatal_report(title: str, text: str) -> None:
    """
    致命错误时，无窗口 exe 里用户什么都看不到 —— 所以：
    1) 尽量写一个日志文件
    2) 弹一个 MessageBox（Windows 上必定可见，不依赖控制台）
    """
    body = f"{text}\n\n" + traceback.format_exc()[-3000:]
    for path in _early_log_candidates():
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] FATAL {title}\n{body}\n")
            body += f"\n\n日志已写入：{path}"
            break
        except Exception:
            continue
    try:
        bc.user32.MessageBoxW(None, body[:2000], title, 0x00000010)  # MB_ICONERROR
    except Exception:
        pass


def _early_log_candidates() -> list[str]:
    """还没确定状态目录时，先把日志往这些地方试。"""
    out = []
    env = os.environ.get("BGCLICK_STATE_DIR")
    if env:
        out.append(os.path.join(env, "bgserver.log"))
    try:
        home = os.path.expanduser("~")
        if home and home != "~":
            out.append(os.path.join(home, ".bgclick", "bgserver.log"))
            out.append(os.path.join(home, "bgserver.log"))
    except Exception:
        pass
    try:
        out.append(os.path.join(tempfile.gettempdir(), "bgserver.log"))
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        out.append(os.path.join(os.path.dirname(sys.executable), "bgserver.log"))
    out.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bgserver.log"))
    return out


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    bc.enable_dpi_awareness()

    try:
        return _main_inner(args, argv)
    except SystemExit:
        raise
    except BaseException as e:
        # ★ 无窗口 exe 绝不能把异常悄悄吞掉 —— 用户会看到「双击没反应」。
        fatal_report("bgclick 服务启动失败",
                     f"{type(e).__name__}: {e}")
        return 1


def _main_inner(args, argv: list[str]) -> int:
    log = Logger(os.path.join(state_dir(), "bgserver.log"),
                 also_stdout=not args.no_console)

    # ★ 托盘默认开启（不再依赖 sys.frozen）。
    #   只有显式给了 --no-tray 才关 —— 比如 CI、无桌面会话、被系统服务方式拉起时。
    if "--no-tray" in argv:
        log.write("已指定 --no-tray：以纯后台模式运行，不创建托盘图标。")
    else:
        log.write("托盘模式：将创建系统托盘图标（想看图标请展开任务栏的 ^ 折叠区）。")

    # 提权（默认行为）。但提权**可能悄悄失败**（沙箱里 ShellExecuteW 会假报成功），
    # 而且在受限沙箱里提权本来也没用 —— 所以这里的逻辑是：
    # 先试提权；只有当「试了会死」的时候才不试。
    if args.auto_elevate and not args._elevated and not bc.is_admin():
        me = bc.self_integrity()
        if me is not None and me < 0x2000:
            # 受限沙箱：提权救不了（UIPI 看的是 IL），直接不提，省得白等
            log.write(f"提示：完整性级别 {bc.integrity_name(me)}（受限环境），"
                      "提权对 UIPI 无效，直接以当前权限启动。")
        else:
            return relaunch_elevated(argv)

    # ★ 重启场景：新进程可能比老进程的端口释放「抢跑」。
    #   带 --_wait-for-port-release 时，先轮询等端口空出来，再走单实例检查。
    #   这是「重启服务只执行到退出、没有重新启动」的直接修复。
    if args._wait_for_port_release:
        deadline = time.time() + 10.0
        waited = False
        while time.time() < deadline:
            if not find_existing(args.port):
                break
            if not waited:
                log.write(f"等待端口 {args.port} 释放（老实例正在退出）……")
                waited = True
            time.sleep(0.3)
        if waited:
            log.write("端口已释放，继续启动。")

    # 单实例检查：端口已有服务就别再起一个
    pid_existing = find_existing(args.port)
    if pid_existing:
        msg = f"端口 {args.port} 上已有服务在运行（PID {pid_existing}），不重复启动。"
        log.write(msg)
        if args.tray:
            # 让用户知道「其实已经在跑了」，而不是以为双击没反应
            try:
                import bgtray
                t = bgtray.TrayApp(title="bgclick", menu_items=[("退出", lambda: None)])
                threading.Thread(target=t.run, daemon=True).start()
                t.wait_ready(3)
                t.notify("bgclick 服务已在运行", msg)
                time.sleep(4)
            except Exception as e:
                log.write(f"提示气泡创建失败：{type(e).__name__}: {e}")
        return 0

    lock = SingleInstance(f"bgclick_server_{args.port}")
    if not lock.acquire():
        log.write(f"已有另一个 bgclick 实例占用端口 {args.port}，退出。")
        return 0

    # 截图根目录：相对路径按**启动时的工作目录**解析，
    # 这样 agent 在哪个工程目录跑，截图就落在那个目录的 shots/ 下，方便直接查看。
    launch_cwd = os.getcwd()

    def _abs_shot(d: str) -> str:
        return os.path.abspath(d if os.path.isabs(d) else os.path.join(launch_cwd, d))

    roots = [_abs_shot(args.shot_dir)]
    roots += [_abs_shot(d) for d in args.extra_shot_dir]
    roots.append(state_dir())
    global ALLOWED_SHOT_ROOTS
    ALLOWED_SHOT_ROOTS = list(dict.fromkeys(os.path.abspath(r) for r in roots))
    for r in ALLOWED_SHOT_ROOTS:
        os.makedirs(r, exist_ok=True)

    token = load_or_create_token(args.token)

    # 只绑本机回环地址 —— 这条是硬安全边界，不要改
    bind_host = "127.0.0.1"
    try:
        httpd = ThreadingHTTPServer((bind_host, args.port), Handler)
    except OSError as e:
        log.write(f"启动失败：端口 {args.port} 可能已被占用（{e}）。")
        if args.tray:
            try:
                import bgtray
                t = bgtray.TrayApp(title="bgclick")
                threading.Thread(target=t.run, daemon=True).start()
                t.wait_ready(3)
                t.notify("bgclick 启动失败", f"端口 {args.port} 被占用")
                time.sleep(4)
            except Exception as e2:
                log.write(f"提示气泡创建失败：{type(e2).__name__}: {e2}")
        lock.release()
        return 1

    httpd.token = token                     # type: ignore[attr-defined]
    httpd.started = time.time()             # type: ignore[attr-defined]
    httpd.verbose = args.verbose            # type: ignore[attr-defined]
    httpd.max_clicks = max(1, args.max_clicks)   # type: ignore[attr-defined]
    httpd.daemon_threads = True

    with open(port_path(), "w", encoding="utf-8") as f:
        f.write(str(args.port))
    with open(pid_path(), "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    elevated = bc.is_admin()
    log.write("=" * 60)
    log.write(f"bgserver {VERSION} 已启动")
    log.write(f"  监听地址 : http://{bind_host}:{args.port}   （仅本机可访问）")
    log.write(f"  权限     : {'管理员 ✔' if elevated else '普通用户 ✘（很多窗口会点不动）'}")
    log.write(f"  完整性级 : {bc.integrity_name(bc.self_integrity())}")
    log.write(f"  托盘     : {'开' if args.tray else '关（--no-tray）'}")
    log.write(f"  token    : {token_path()}")
    log.write(f"  截图目录 : {', '.join(ALLOWED_SHOT_ROOTS)}")
    log.write(f"  日志     : {log.path}")
    log.write("=" * 60)

    # 服务跑在后台线程；托盘消息循环要占主线程（Win32 托盘消息必须由创建它的线程处理）
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    def shutdown_all():
        try:
            httpd.shutdown()
        except Exception:
            pass
        try:
            httpd.server_close()
        except Exception:
            pass
        for p in (port_path(), pid_path()):
            try:
                os.remove(p)
            except OSError:
                pass
        lock.release()
        log.write("服务已停止。")

    # 无托盘模式：主线程只是等服务器退出（Ctrl+C 或 /shutdown 接口）
    if not args.tray:
        try:
            while server_thread.is_alive():
                server_thread.join(0.5)
        except KeyboardInterrupt:
            log.write("收到 Ctrl+C，正在停止……")
        shutdown_all()
        return 0

    # ---------- 托盘模式 ----------
    # ★ 托盘创建失败**不再静默退回无托盘**：写清楚日志、弹 MessageBox、
    #   并继续以无托盘模式跑服务（服务本身不能因为托盘的 UI 问题而停）。
    tray = None
    try:
        import bgtray
        from bgtray import _open_path
    except Exception as e:
        log.write(f"★ 无法导入 bgtray：{type(e).__name__}: {e}")
        log.write("  服务继续以无托盘模式运行。检查 bgtray.py 是否在脚本同目录。")
        try:
            bc.user32.MessageBoxW(
                None,
                f"托盘模块 bgtray.py 加载失败，服务将以无托盘模式继续运行。\n\n"
                f"{type(e).__name__}: {e}\n\n日志：{log.path}",
                "bgclick 托盘启动失败", 0x00000030)  # MB_ICONWARNING
        except Exception:
            pass
        try:
            while server_thread.is_alive():
                server_thread.join(0.5)
        except KeyboardInterrupt:
            log.write("收到 Ctrl+C，正在停止……")
        shutdown_all()
        return 0

    def do_shutdown():
        """从托盘菜单退出：先关服务，再让托盘消息循环结束。"""
        log.write("从托盘菜单请求退出……")
        shutdown_all()
        if tray is not None:
            tray.quit()

    def do_restart():
        """
        从托盘菜单重启。

        ★ 修复「只退出没重启」：
          之前的实现是 Popen(新进程) → do_shutdown()。
          问题在于 httpd.shutdown() 是**异步**的 —— 端口不会立即释放，
          新进程启动时跑 find_existing(port) 还能探到老进程，于是判定
          「已有实例在跑」并直接退出；随后老进程才真正关闭。结果就是
          看到「进程退出了但没重启」。

          现在给新进程加 --_wait-for-port-release 标记：它启动后会轮询
          /health，直到老进程的端口彻底不响应，再继续走单实例检查。
          这样就不会误判。
        """
        log.write("从托盘菜单请求重启……")
        try:
            exe = sys.executable
            base = os.path.basename(exe).lower()
            if base.startswith("pythonw"):
                cand = os.path.join(os.path.dirname(exe), "python.exe")
                if os.path.exists(cand):
                    exe = cand

            # 清掉会干扰新实例的参数：
            #   --_elevated                让新进程自己重新走提权逻辑
            #   --_wait-for-port-release   如果老进程是用它启动的，别带过去
            clean_argv = [a for a in argv
                          if a not in ("--_elevated", "--_wait-for-port-release")]
            clean_argv.append("--_wait-for-port-release")

            if getattr(sys, "frozen", False):
                cmd = [exe] + clean_argv
            else:
                cmd = [exe, os.path.abspath(__file__)] + clean_argv

            log.write(f"拉起新实例：{subprocess.list2cmdline(cmd)}")
            subprocess.Popen(cmd, cwd=launch_cwd, close_fds=True)
        except Exception as e:
            log.write(f"重启失败（拉起新进程时出错）：{type(e).__name__}: {e}")
            try:
                if tray is not None:
                    tray.notify("重启失败", f"{type(e).__name__}: {e}")
            except Exception:
                pass
            return   # 新进程都没起来，别把当前实例关了

        # 新进程已经拉起（它会等端口释放）。现在关掉自己，让出端口。
        do_shutdown()

    def status_text() -> str:
        return (f"{'管理员' if elevated else '普通用户'} / "
                f"{bc.integrity_name(bc.self_integrity())} / "
                f"端口 {args.port}")

    try:
        tray = bgtray.TrayApp(
            title="bgclick 服务",
            tooltip=f"bgclick 服务运行中（{'管理员' if elevated else '普通用户'}，端口 {args.port}）",
            icon_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "bgclick.ico"),
            menu_items=[
                ("查看状态（双击我）", lambda: _open_path(log.path)),
                ("-", lambda: None),
                ("打开日志", lambda: _open_path(log.path)),
                ("打开截图目录", lambda: _open_path(ALLOWED_SHOT_ROOTS[0])),
                ("打开状态目录", lambda: _open_path(state_dir())),
                ("复制 Token 路径", lambda: _open_path(token_path())),
                ("-", lambda: None),
                ("重启服务", do_restart),
                ("退出（停止服务）", do_shutdown),
            ],
            on_quit=shutdown_all,
            on_status=status_text,
        )
    except Exception as e:
        log.write(f"★ 托盘对象创建失败：{type(e).__name__}: {e}")
        log.write(traceback.format_exc()[-1500:])
        try:
            bc.user32.MessageBoxW(
                None,
                f"托盘初始化失败，服务将以无托盘模式继续运行。\n\n"
                f"{type(e).__name__}: {e}\n\n日志：{log.path}",
                "bgclick 托盘启动失败", 0x00000030)
        except Exception:
            pass
        try:
            while server_thread.is_alive():
                server_thread.join(0.5)
        except KeyboardInterrupt:
            log.write("收到 Ctrl+C，正在停止……")
        shutdown_all()
        return 0

    # ★ _open_path 现在会抛异常（不再静默吞），托盘回调的 try 能接住。
    #   为了让菜单项调用的 _open_path 走托盘回调的异常处理，这里再包一层，
    #   保证任何异常都能弹气泡告知用户。
    import functools

    def _menu_open(path):
        def _cb():
            try:
                _open_path(path)
            except Exception as e:
                # 自己再兜一层，保证「点了没反应」变成「弹气泡说为什么」
                try:
                    tray.notify("打开失败", f"{type(e).__name__}: {e}")
                except Exception:
                    pass
                # 同时写日志，日志是最可靠的线索
                log.write(f"打开路径失败：{path} -> {type(e).__name__}: {e}")
        return _cb

    # 重新构造菜单项，把 _open_path 的调用换成带兜底的包装
    tray.menu_items = [
        ("查看状态（双击我）", _menu_open(log.path)),
        ("-", lambda: None),
        ("打开日志", _menu_open(log.path)),
        ("打开截图目录", _menu_open(ALLOWED_SHOT_ROOTS[0])),
        ("打开状态目录", _menu_open(state_dir())),
        ("复制 Token 路径", _menu_open(token_path())),
        ("-", lambda: None),
        ("重启服务", do_restart),
        ("退出（停止服务）", do_shutdown),
    ]

    try:
        tray.run()          # 阻塞直到托盘退出
    except KeyboardInterrupt:
        log.write("收到 Ctrl+C，正在停止……")
        shutdown_all()
    except Exception as e:
        log.write(f"★ 托盘消息循环异常：{type(e).__name__}: {e}")
        log.write(traceback.format_exc()[-1500:])
        try:
            bc.user32.MessageBoxW(
                None,
                f"托盘消息循环崩溃，服务将以无托盘模式继续运行。\n\n"
                f"{type(e).__name__}: {e}\n\n日志：{log.path}",
                "bgclick 托盘异常", 0x00000030)
        except Exception:
            pass
        try:
            while server_thread.is_alive():
                server_thread.join(0.5)
        except KeyboardInterrupt:
            pass
        shutdown_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())