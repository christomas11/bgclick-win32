# -*- coding: utf-8 -*-
"""bgkit.bgclient.bootstrap.py —— 自动拉起服务（让 skill 用起来像一条命令）

从 bgclient.py 拆出的一节（源文件第 133-213 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

from bgkit import root_file
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from .paths import DEFAULT_HOST, read_port, state_dir



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

    server_py = root_file("bgserver.py", "窗口工作.py")
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
