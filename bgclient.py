#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bgclient.py —— bgserver 的命令行客户端（也给 agent skill 当调用入口）

零依赖（纯标准库），所以 skill 里直接 subprocess 调它就行。

常用
----
  python bgclient.py health                       # 服务活着吗、是不是管理员
  python bgclient.py windows                      # 列出窗口（含能否点击）
  python bgclient.py click --title "记事本" --pos 400,300
  python bgclient.py shot --title "记事本" --out shots/note.png
  python bgclient.py text --title "记事本" --text "hello" --enter
  python bgclient.py shutdown                     # 停服务

自动化友好
----------
  * 退出码：0 成功，1 用法/未找到，2 参数错误，3 服务未启动，4 权限被拒
  * --json 输出机器可读结果（默认就是 JSON，其实是给 agent 用的）
  * 所有失败都带 {"ok": false, "error": "..."} 而不是抛栈

供 skill 调用的推荐形态：直接 import 这个文件，或者 subprocess 跑它。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
TIMEOUT = 30


def state_dir() -> str:
    """
    状态目录（token / 端口 / pid）。

    必须和 bgserver.py 里的 state_dir() **完全一致**（两边用同一套回退链，
    否则客户端和服务会读到不同的 token）。

    回退顺序：
      1. 环境变量 BGCLICK_STATE_DIR（显式指定，最优先）
      2. ~/.bgclick                     （正常情况，用 expanduser("~")，
                                          与 bgserver 保持一致；不依赖 USERPROFILE）
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

    last_err: Exception | None = None
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            # 确认真的能写（makedirs 成功不代表可写，比如目录已存在但只读）
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
        + f"\n最后一个错误：{last_err}\n"
        "可以用环境变量指定：set BGCLICK_STATE_DIR=D:\\some\\writable\\dir"
    )


# --------------------------------------------------------------------------
# 连接信息
# --------------------------------------------------------------------------


def read_token() -> str:
    try:
        p = os.path.join(state_dir(), "token.txt")
    except Exception as e:
        raise FileNotFoundError(
            f"找不到可写的状态目录，没法存 token：{e}\n"
            "可以用环境变量指定：set BGCLICK_STATE_DIR=D:\\some\\writable\\dir") from e
    if not os.path.exists(p):
        raise FileNotFoundError(
            "找不到 token 文件。服务似乎还没启动过。\n"
            "先运行：python bgserver.py")
    with open(p, "r", encoding="utf-8") as f:
        t = f.read().strip()
    if not t:
        raise FileNotFoundError("token 文件是空的。删掉它再启动服务试试。")
    return t


def read_port() -> int:
    try:
        p = os.path.join(state_dir(), "port.txt")
    except Exception:
        return DEFAULT_PORT
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return int(f.read().strip())
        except Exception:
            pass
    return DEFAULT_PORT


# --------------------------------------------------------------------------
# 自动拉起服务（让 skill 用起来像"一条命令"）
# --------------------------------------------------------------------------


def _ping(host: str, port: int, timeout: float = 2.0) -> bool:
    """轻量探活，不需要 token。"""
    try:
        req = urllib.request.Request(f"http://{host}:{port}/health")
        req.add_header("Host", f"127.0.0.1:{port}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")).get("ok") is True
    except Exception:
        return False


def ensure_server(host: str = DEFAULT_HOST, port: int | None = None,
                  autostart: bool = True, wait: float = 45.0) -> dict:
    """
    确保服务在跑。没跑就把它拉起来（会自动弹 UAC）。

    why: skill 应该能用一条命令完成事情，而不是先教用户去启动服务。
    返回 {"started": bool, "port": int}；起不来则抛 ConnectionError。
    """
    port = port if port is not None else read_port()

    if _ping(host, port):
        return {"started": False, "port": port}

    if not autostart:
        raise ConnectionError(
            f"服务没有运行（{host}:{port}）。启动它：python bgserver.py")

    server_py = os.path.join(HERE, "bgserver.py")
    if not os.path.exists(server_py):
        raise ConnectionError(f"找不到 {server_py}")

    # 用 python.exe 而不是 pythonw —— 提权后的窗口要有控制台，用户才看得到进度
    exe = sys.executable
    if os.path.basename(exe).lower().startswith("pythonw"):
        cand = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(cand):
            exe = cand

    # 脱离父进程启动，否则它会被调用方一起收掉
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    # ★ 把状态目录显式传给服务端。
    #   不能让它自己再算一遍 —— 沙箱/不同工作目录下两边会算出不同的目录，
    #   结果就是 token 对不上、请求 401。这个坑实测踩过。
    caller_cwd = os.getcwd()
    env = os.environ.copy()
    env["BGCLICK_STATE_DIR"] = state_dir()
    args = [exe, server_py, "--port", str(port),
            "--shot-dir", os.path.join(caller_cwd, "shots")]
    try:
        subprocess.Popen(
            args, cwd=caller_cwd, env=env,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True,
        )
    except Exception as e:
        raise ConnectionError(f"启动服务失败：{e}") from e

    # 等服务起来（用户可能正在对着 UAC 弹窗点「是」）
    deadline = time.time() + wait
    announced = False
    while time.time() < deadline:
        if _ping(host, port):
            return {"started": True, "port": port}
        if not announced and time.time() > deadline - wait + 3:
            sys.stderr.write("正在启动 bgserver（可能会弹 UAC，请点「是」）……\n")
            announced = True
        time.sleep(0.5)

    raise ConnectionError(
        f"等了 {wait:.0f} 秒服务还没起来。可能 UAC 被拒了，或端口 {port} 被占用。\n"
        f"手动排查：python \"{server_py}\" --port {port}")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class Client:
    def __init__(self, host: str = DEFAULT_HOST, port: int | None = None,
                 token: str | None = None, timeout: float = TIMEOUT):
        self.host = host
        self.port = port if port is not None else read_port()
        self.token = token if token is not None else read_token()
        self.timeout = timeout

    @property
    def base(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        # Host 头必须写成 localhost/127.0.0.1 —— 服务端会校验（防 DNS rebinding）
        req.add_header("Host", f"127.0.0.1:{self.port}")
        req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"ok": False, "error": raw or f"HTTP {e.code}"}
            payload.setdefault("http_status", e.code)
            if e.code == 401:
                # 最常见的成因：端口上跑着一个**旧服务**，它的 token 在别的状态目录里。
                # 光说"token 错误"会让人查半天，所以直接把排查方向写出来。
                payload["error"] = (
                    f"{payload.get('error', 'token 校验失败')}\n"
                    f"提示：端口 {self.port} 上可能跑着一个旧的服务实例，"
                    f"而它的 token 不在当前状态目录里。\n"
                    f"  当前状态目录：{state_dir()}\n"
                    f"  处理办法：先 python bgclient.py shutdown 停掉旧实例，再重试。\n"
                    f"  若停不掉：netstat -ano | findstr :{self.port} 找到 PID，"
                    f"taskkill /PID <pid> /F"
                )
            return payload
        except urllib.error.URLError as e:
            raise ConnectionError(
                f"连不上 {self.base}（{e.reason}）。服务可能没启动，"
                f"试试：python bgserver.py") from e

    # --- 接口 ---
    def health(self) -> dict:
        """health 不需要 token，但带上也无妨。"""
        req = urllib.request.Request(self.base + "/health")
        req.add_header("Host", f"127.0.0.1:{self.port}")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            raise ConnectionError(f"连不上 {self.base}（{e}）。服务没启动？") from e

    def windows(self) -> dict:
        return self._request("GET", "/windows")

    def window(self, **kw) -> dict:
        return self._request("POST", "/window", kw)

    def probe(self, **kw) -> dict:
        return self._request("POST", "/probe", kw)

    def click(self, **kw) -> dict:
        return self._request("POST", "/click", kw)

    def screenshot(self, **kw) -> dict:
        return self._request("POST", "/screenshot", kw)

    def text(self, **kw) -> dict:
        return self._request("POST", "/text", kw)

    def shutdown(self) -> dict:
        return self._request("GET", "/shutdown")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def parse_pair(s: str) -> list[int]:
    try:
        a, b = s.replace("，", ",").split(",")
        return [int(a.strip()), int(b.strip())]
    except Exception:
        raise argparse.ArgumentTypeError(f"坐标格式应为 X,Y，收到 {s!r}")


def add_target_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--title", help="窗口标题（默认子串匹配）")
    p.add_argument("--process", help="进程名，如 notepad.exe")
    p.add_argument("--hwnd", help="窗口句柄（十进制或 0x 开头）")
    p.add_argument("--exact", action="store_true", help="标题完全匹配")
    p.add_argument("--regex", action="store_true", help="标题按正则匹配")
    p.add_argument("--index", type=int, default=0, help="匹配多个时选第几个")


def target_payload(args) -> dict:
    body: dict = {"index": args.index}
    if args.title:
        body["title"] = args.title
    if args.process:
        body["process"] = args.process
    if args.hwnd:
        body["hwnd"] = int(args.hwnd, 0)
    if args.exact:
        body["exact"] = True
    if args.regex:
        body["regex"] = True
    return body


def emit(payload: dict, as_json: bool, human: str | None = None) -> int:
    """输出结果并返回退出码。"""
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    elif human is not None:
        print(human)
    else:
        print(json.dumps(payload, ensure_ascii=False, default=str))

    if payload.get("ok"):
        return 0

    # 退出码推断：优先看服务端显式给的 error_code，字符串匹配只做兜底。
    # 服务端在 bc.AppError.code 里已经带了语义（见 bgserver 各处 raise），
    # 但 AppError 走的是 400 响应，所以服务端把 code 也放进了 body。
    err = str(payload.get("error", ""))
    http_status = payload.get("http_status")

    if http_status == 401:
        return 4
    if http_status in (400, 403):
        # 400 通常表示服务端 AppError，但里面混着"没找到窗口"(1) 和"参数错"(2)。
        # 看服务端有没有给 body["code"]，有就按它来。
        inner = payload.get("code")
        if isinstance(inner, int):
            return 2 if inner == 2 else 1
        return 2
    if http_status is not None and http_status >= 500:
        return 1
    if "token" in err.lower():
        return 4
    return 1


def build_parser() -> argparse.ArgumentParser:
    # 公共参数（--human 放在主命令前后都能用）
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--human", action="store_true", help="人类可读输出而非 JSON")

    p = argparse.ArgumentParser(
        prog="bgclient.py",
        description="bgserver 客户端（默认输出 JSON，方便 agent 调用）",
        parents=[common],
    )
    p.add_argument("--host", default=DEFAULT_HOST, help="服务地址（默认 127.0.0.1）")
    p.add_argument("--port", type=int, help="端口（默认读 .bgstate/port.txt）")
    p.add_argument("--token", help="token（默认读 .bgstate/token.txt）")
    p.add_argument("--no-autostart", dest="autostart", action="store_false", default=True,
                   help="服务没跑时不自动启动（默认会自动拉起，会弹 UAC）")

    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name: str, help_text: str):
        return sub.add_parser(name, help=help_text, parents=[common])

    add("health", "探活")
    add("shutdown", "停止服务")

    sp = add("windows", "列出所有可见窗口")
    sp.add_argument("--only-postable", action="store_true", help="只列能点击的")

    sp = add("window", "查单个窗口详情")
    add_target_args(sp)

    sp = add("probe", "探测某窗口能否接收消息")
    add_target_args(sp)

    sp = add("click", "点击")
    add_target_args(sp)
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--pos", type=parse_pair, help="客户区坐标 X,Y")
    g.add_argument("--center", action="store_true", help="点客户区中心")
    g.add_argument("--screen-pos", type=parse_pair, help="屏幕绝对坐标 X,Y")
    sp.add_argument("--count", type=int, default=1, help="点击次数，0=循环（默认 1）")
    sp.add_argument("--interval", type=float, default=0.5, help="间隔秒（默认 0.5）")
    sp.add_argument("--button", default="left", choices=["left", "right", "middle"])
    sp.add_argument("--method", default="post", choices=["post", "send", "hardware"])
    sp.add_argument("--jitter", type=int, default=0, help="像素抖动")
    sp.add_argument("--activate", action="store_true", help="点击前激活窗口")
    sp.add_argument("--force-continue", action="store_true", help="失败也继续")

    sp = add("shot", "截图")
    add_target_args(sp)
    sp.add_argument("--out", help="输出路径（相对路径基于服务的截图目录）")
    sp.add_argument("--format", default="png", choices=["png", "bmp"])
    sp.add_argument("--method", default="auto", choices=["auto", "print", "bitblt"])
    sp.add_argument("--client-only", action="store_true", help="只截客户区")
    sp.add_argument("--inline", action="store_true", help="结果里附 base64 图")

    sp = add("text", "向窗口输入文本")
    add_target_args(sp)
    sp.add_argument("--text", required=True, help="要输入的文本")
    sp.add_argument("--enter", action="store_true", help="末尾补一个回车")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    as_json = not args.human

    # health 不需要 token，也不该因为缺 token 就失败 —— 先处理它
    if args.cmd == "health":
        try:
            bare = Client(args.host, args.port, token="unused")
            info = bare.health()
        except ConnectionError as e:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            return 3
        except Exception as e:
            print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"},
                             ensure_ascii=False))
            return 3
        human = (f"服务在线：bgserver {info.get('version')}  "
                 f"权限={'管理员' if info.get('elevated') else '普通用户'}  "
                 f"完整性={info.get('integrity')}  "
                 f"PID={info.get('pid')}  已运行 {info.get('uptime')}s")
        return emit(info, as_json, human)

    # 其余命令：服务没跑就自动拉起（skill 用起来才像一条命令）
    if args.cmd != "shutdown":
        try:
            if not _ping(args.host, args.port or read_port()):
                ensure_server(args.host, args.port, autostart=args.autostart)
        except ConnectionError as e:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            return 3

    # 服务可能是刚被拉起来的，token 文件这时才存在 —— 所以放在这之后再读
    try:
        client = Client(args.host, args.port, args.token)
    except FileNotFoundError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 3

    try:
        if args.cmd == "shutdown":
            r = client.shutdown()
            return emit(r, as_json, "已通知服务停止。")

        if args.cmd == "windows":
            r = client.windows()
            if args.only_postable:
                r["windows"] = [w for w in r.get("windows", []) if w.get("postable")]
                r["count"] = len(r["windows"])
            if not as_json:
                lines = [f"{r.get('count', 0)} 个窗口"]
                for w in r.get("windows", []):
                    cw, ch = w.get("client_size", [0, 0])
                    lines.append(f"  {'✔' if w.get('postable') else '✘'} "
                                 f"{w.get('hwnd_hex', ''):<12}{w.get('process', '')[:20]:<22}"
                                 f"{cw}x{ch:<8}{w.get('title', '')[:40]}")
                print("\n".join(lines))
                return 0
            return emit(r, as_json)

        if args.cmd == "window":
            return emit(client.window(**target_payload(args)), as_json)

        if args.cmd == "probe":
            return emit(client.probe(**target_payload(args)), as_json)

        if args.cmd == "click":
            body = target_payload(args)
            if args.pos:
                body["pos"] = args.pos
            elif args.center:
                body["center"] = True
            else:
                body["screen_pos"] = args.screen_pos
            body.update({"count": args.count, "interval": args.interval,
                         "button": args.button, "method": args.method,
                         "jitter": args.jitter, "activate": args.activate,
                         "force_continue": args.force_continue})
            r = client.click(**body)
            human = None
            if r.get("ok"):
                res = r.get("result", {})
                human = (f"已点击 {res.get('clicked')} 次"
                         f"（目标 {res.get('title')}）坐标 {res.get('base_pos')}")
            return emit(r, as_json, human)

        if args.cmd == "shot":
            body = target_payload(args)
            if args.out:
                body["path"] = args.out
            body.update({"format": args.format, "method": args.method,
                         "client_only": args.client_only, "inline": args.inline})
            r = client.screenshot(**body)
            human = None
            if r.get("ok"):
                s = r["screenshot"]
                human = (f"已截图 {s['path']}\n"
                         f"  {s['width']}x{s['height']} {s['format'].upper()} "
                         f"{s['bytes']} 字节  方式 {s['capture_method']}")
            return emit(r, as_json, human)

        if args.cmd == "text":
            body = target_payload(args)
            body.update({"text": args.text, "enter": args.enter})
            return emit(client.text(**body), as_json)

        return 1

    except (ConnectionError, urllib.error.URLError) as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 3
    except FileNotFoundError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 3
    except KeyboardInterrupt:
        return 130
    except Exception as e:      # 兜底：绝不给调用方甩 traceback
        print(json.dumps({"ok": False,
                          "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())