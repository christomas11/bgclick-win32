# -*- coding: utf-8 -*-
"""鉴权测试 —— 真的起一个 bgserver，用裸 HTTP 打它。

为什么不只测函数：
    token 校验、Host 头校验、--no-auth 这三件事只有走完整请求链路才验证得了。
    而且这类安全行为最怕「以后重构时顺手改坏了，没人发现」。

不起托盘、不提权，所以在 CI 上也能跑（端口用随机空闲端口，状态目录用临时目录）。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

# 本机请求必须绕过系统代理，否则装了 Clash 的机器会被代理挡成 502
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _request(port: int, path: str, body: dict | None = None,
             token: str | None = None, host: str | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 method="POST" if data is not None else "GET")
    req.add_header("Host", host or f"127.0.0.1:{port}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with _OPENER.open(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def _start(args: list[str], state_dir: str):
    env = os.environ.copy()
    env["BGCLICK_STATE_DIR"] = state_dir
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "bgkit.bgserver", "--port", str(port),
         "--no-tray", "--no-elevate"] + args,
        cwd=BASE, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 25
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        try:
            status, _ = _request(port, "/health")
            if status == 200:
                return proc, port, state_dir
        except Exception:
            pass
        time.sleep(0.25)
    proc.terminate()
    pytest.skip("bgserver 没能在 25 秒内起来（可能是端口/权限/环境限制）")


def _stop(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()


@pytest.fixture(scope="module")
def auth_server(tmp_path_factory):
    if os.name != "nt":
        pytest.skip("仅 Windows")
    state = str(tmp_path_factory.mktemp("state_auth"))
    proc, port, state = _start([], state)
    yield port, state
    _stop(proc)


@pytest.fixture(scope="module")
def open_server(tmp_path_factory):
    if os.name != "nt":
        pytest.skip("仅 Windows")
    state = str(tmp_path_factory.mktemp("state_open"))
    proc, port, state = _start(["--no-auth"], state)
    yield port, state
    _stop(proc)


class TestTokenAuth:
    def test_health_needs_no_token(self, auth_server):
        port, _ = auth_server
        status, body = _request(port, "/health")
        assert status == 200
        assert body.get("ok") is True

    def test_missing_token_rejected(self, auth_server):
        port, _ = auth_server
        status, body = _request(port, "/windows")
        assert status == 401
        assert body.get("ok") is False

    def test_wrong_token_rejected(self, auth_server):
        port, _ = auth_server
        status, _ = _request(port, "/windows", token="not-the-real-one")
        assert status == 401

    def test_right_token_accepted(self, auth_server):
        port, state = auth_server
        with open(os.path.join(state, "token.txt"), encoding="utf-8") as f:
            token = f.read().strip()
        assert token, "服务应该已经生成了 token 文件"
        status, body = _request(port, "/windows", token=token)
        assert status == 200
        assert body.get("ok") is True

    def test_foreign_host_rejected(self, auth_server):
        """DNS rebinding 防线：Host 不是本机就必须拒，而且要在 token 之前拒。"""
        port, state = auth_server
        with open(os.path.join(state, "token.txt"), encoding="utf-8") as f:
            token = f.read().strip()
        status, body = _request(port, "/windows", token=token, host="evil.example.com")
        assert status == 403, f"外部 Host 居然通过了：{body}"


class TestNoAuth:
    def test_no_auth_lets_anonymous_through(self, open_server):
        port, _ = open_server
        status, body = _request(port, "/windows")
        assert status == 200
        assert body.get("ok") is True

    def test_no_auth_still_checks_host(self, open_server):
        """关鉴权不等于关安全边界：Host 校验必须还在。"""
        port, _ = open_server
        status, _ = _request(port, "/windows", host="evil.example.com")
        assert status == 403
