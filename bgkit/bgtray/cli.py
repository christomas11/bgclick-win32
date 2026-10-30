# -*- coding: utf-8 -*-
"""bgkit.bgtray.cli.py —— 独立的托盘控制台入口

和 `bgserver` 自带的那块托盘的区别：
  * bgserver 的托盘是「服务的一部分」，服务死了托盘也没了
  * 这个是「服务的遥控器」，服务没跑也能开，还能顺手把它拉起来

之前顶层 bgclick.py / bgtray.py 里写着 `from bgkit.bgtray.cli import main`，
但这个模块根本不存在 —— `python bgtray.py` 会直接 ImportError。此文件补上。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import Optional

from bgkit import ROOT, __version__, asset_path, script_or_module
from bgkit.bgclient.http import Client
from bgkit.bgclient.paths import read_port, read_token, state_dir
from bgkit.bgtray.app import TrayApp
from bgkit.bgtray.shell import _open_path


def _client(args) -> Client:
    return Client(args.host, args.port or read_port(), token=read_token())


def _status_text(args) -> str:
    """查一次服务状态，返回给人看的一行。连不上也不抛异常。"""
    try:
        info = _client(args).health()
    except Exception as e:
        return f"服务未运行（{type(e).__name__}: {e}）"
    if not info.get("ok", True):
        return f"服务异常：{info.get('error')}"
    return (f"在线 {info.get('version')} / "
            f"{'管理员' if info.get('elevated') else '普通用户'} / "
            f"端口 {args.port or read_port()} / 已运行 {info.get('uptime')}s")


def _restart(args, tray: Optional[TrayApp] = None) -> None:
    """先让老服务退出，再拉起一个新的。"""
    try:
        _client(args).shutdown()
    except Exception:
        pass  # 本来就没在跑，正好
    head = script_or_module("bgserver.py", "窗口工作.py", module="bgkit.bgserver")
    cmd = [sys.executable] + head + ["--port", str(args.port or read_port())]
    subprocess.Popen(cmd, cwd=os.getcwd(), close_fds=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bgtray",
        description="bgclick 服务托盘控制台：看状态、开日志、重启、退出。",
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=0, help="默认读端口文件")
    p.add_argument("--icon", default="", help="托盘图标路径，默认用包内 bgclick.ico")
    p.add_argument("--status", action="store_true", help="只查一次状态就退出（不开托盘）")
    p.add_argument("--version", action="version", version=f"bgkit {__version__}")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.status:
        print(_status_text(args))
        return 0

    icon = args.icon or asset_path("bgclick.ico")

    def show_status() -> None:
        tray = _holder.get("tray")
        if tray is not None:
            tray.notify("bgclick 服务", _status_text(args))

    _holder: dict = {}

    tray = TrayApp(
        title="bgclick 控制台",
        tooltip="bgclick 控制台（双击看状态）",
        icon_path=icon,
        menu_items=[
            ("查看状态（双击我）", show_status),
            ("-", lambda: None),
            ("打开日志", lambda: _open_path(os.path.join(ROOT, "bgserver.log"))),
            ("打开状态目录", lambda: _open_path(state_dir())),
            ("-", lambda: None),
            ("重启服务", lambda: _restart(args, tray)),
            ("退出", lambda: None),   # TrayApp 会在 on_quit 里收尾
        ],
    )
    _holder["tray"] = tray
    tray.set_tooltip(_status_text(args)[:120])
    tray.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
