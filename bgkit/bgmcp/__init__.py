# -*- coding: utf-8 -*-
"""bgkit.bgmcp —— bgclick 的 MCP（Model Context Protocol）服务端

把 bgclick 已有的 HTTP 接口包成 MCP 工具，让 Claude Desktop / Cursor /
任何支持 MCP 的客户端直接调用「后台点击 Windows 窗口」的能力。

入口：python -m bgkit.bgmcp（或装包后的 bgmcp 命令）
"""

from __future__ import annotations

from .server import TOOLS, McpServer, main, serve

__all__ = ["McpServer", "TOOLS", "main", "serve"]
