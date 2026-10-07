# -*- coding: utf-8 -*-
"""bgkit.bgclient.http.py —— HTTP 客户端

从 bgclient.py 拆出的一节（源文件第 214-306 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from .paths import DEFAULT_HOST, TIMEOUT, read_port, read_token, state_dir



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

    def key(self, **kw) -> dict:
        return self._request("POST", "/key", kw)

    def mouse(self, **kw) -> dict:
        return self._request("POST", "/mouse", kw)

    def uia(self, **kw) -> dict:
        return self._request("POST", "/uia", kw)

    def shutdown(self) -> dict:
        return self._request("GET", "/shutdown")
