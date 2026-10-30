# -*- coding: utf-8 -*-
"""bgkit.bgmcp.server.py —— 把 bgclick 包成 MCP server（stdio 传输，零第三方依赖）

协议：JSON-RPC 2.0，一行一条消息，UTF-8，不缓冲。
支持的方法：initialize / notifications/* / ping / tools/list / tools/call

为什么不引官方 mcp SDK：
    这个项目从第一天起就是「零第三方依赖」（连 PNG 编码都是拿 zlib 手写的）。
    为了接 MCP 拉一个 SDK 进来，等于把最大的卖点换掉了。协议本身很简单，
    自己实现一百多行足够，还顺带避免了 SDK 版本跟不上协议演进的麻烦。

stdout 是协议通道，任何 print 都会把客户端搞崩 —— 所以本模块开头就把
sys.stdout 换成了 stderr，真正的协议流单独攥在手里。
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Optional

from bgkit import __version__
from bgkit.bgclient.bootstrap import ensure_server
from bgkit.bgclient.http import Client

SERVER_NAME = "bgclick"
DEFAULT_PROTOCOL = "2024-11-05"
KNOWN_PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18"}

# 目标选择器：所有涉及窗口的工具共用这一组参数
_TARGET_PROPS = {
    "hwnd": {"type": ["integer", "string"],
             "description": "窗口句柄，如 0x20400 或 132096。最省事的定位方式。"},
    "title": {"type": "string", "description": "按标题匹配（默认子串、忽略大小写）"},
    "process": {"type": "string", "description": "按进程名精确匹配，如 notepad.exe"},
    "regex": {"type": "boolean", "description": "title 按正则解释"},
    "exact": {"type": "boolean", "description": "title 要求完全相等"},
    "index": {"type": "integer", "description": "匹配到多个时的第几个，默认 0"},
    "include_hidden": {"type": "boolean", "description": "连隐藏窗口一起匹配"},
}


def _t(name: str, desc: str, props: dict, required: Optional[list] = None,
       extra: Optional[dict] = None) -> dict:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {**props, **(extra or {})},
        "additionalProperties": True,   # 允许透传底层参数，不用件件都在这里列
    }
    if required:
        schema["required"] = required
    return {"name": name, "description": desc, "inputSchema": schema}


TOOLS: list[dict] = [
    _t(
        "list_windows",
        "列出当前可见的顶层窗口，含句柄、进程、标题、客户区尺寸，以及"
        "「能否后台投递消息」。做任何操作前先调这个拿 hwnd。",
        {
            "filter": {"type": "string",
                       "description": "按标题或进程名模糊过滤，不填返回全部"},
            "only_postable": {"type": "boolean",
                              "description": "只返回能后台投递的窗口"},
        },
    ),
    _t(
        "window_info",
        "查单个窗口的详细信息：位置、尺寸、完整性级别、能否后台投递、被拦的原因。",
        _TARGET_PROPS,
    ),
    _t(
        "probe",
        "对窗口做一次投递探针（发一条零副作用的 WM_MOUSEMOVE），"
        "确认 UIPI 是否拦截。点击前想先确认可行性就用它。",
        _TARGET_PROPS,
    ),
    _t(
        "click",
        "后台点击窗口。默认走 PostMessage 投递，不动你的光标和焦点。"
        "坐标是客户区坐标（左上角为 0,0），可用 x/y、center 或 uia_name 指定位置。",
        _TARGET_PROPS,
        extra={
            "x": {"type": "integer", "description": "客户区 X"},
            "y": {"type": "integer", "description": "客户区 Y"},
            "center": {"type": "boolean", "description": "点客户区正中心"},
            "uia_name": {"type": "string",
                         "description": "用 UIA 元素名定位，比坐标稳（窗口挪了也不怕）"},
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
            "method": {
                "type": "string",
                "enum": ["post", "send", "sendinput", "hardware"],
                "description": "post=后台投递(默认,不动光标)；send=同步投递；"
                               "sendinput/hardware=真输入(会占光标，但对所有程序有效)",
            },
            "count": {"type": "integer", "description": "连点次数，默认 1"},
            "interval": {"type": "number", "description": "连点间隔秒数，默认 0.5"},
            "fake_activate": {"type": "boolean",
                              "description": "点击前先投一条 WM_ACTIVATE 假激活"},
        },
    ),
    _t(
        "screenshot",
        "截取窗口画面（PrintWindow，被别的窗口盖住也能抓到）。"
        "默认存文件并返回路径；inline=true 时直接返回图片数据给模型看。",
        _TARGET_PROPS,
        extra={
            "path": {"type": "string", "description": "保存路径，默认按进程名+句柄命名"},
            "client_only": {"type": "boolean", "description": "只截客户区"},
            "format": {"type": "string", "enum": ["png", "bmp"]},
            "method": {"type": "string", "enum": ["auto", "print", "bitblt"]},
            "inline": {"type": "boolean", "description": "返回 base64 图片内容"},
        },
    ),
    _t(
        "type_text",
        "向窗口输入文本（逐字符 WM_CHAR），支持中文和 emoji。"
        "不抢焦点，适合往输入框填内容。",
        _TARGET_PROPS,
        required=["text"],
        extra={
            "text": {"type": "string", "description": "要输入的文本"},
            "interval": {"type": "number", "description": "每字符间隔秒数"},
            "enter": {"type": "boolean", "description": "输完补一个回车"},
        },
    ),
    _t(
        "send_key",
        "发送按键或组合键，如 ctrl+s、alt+F4、F5。支持多键同按和长按。",
        _TARGET_PROPS,
        extra={
            "keys": {"type": "string", "description": "按键字符串，如 ctrl+shift+s"},
            "chord": {"type": "string", "description": "组合键（同时按下）"},
            "repeat": {"type": "integer", "description": "重复次数"},
            "hold": {"type": "number", "description": "按住秒数"},
            "scancode": {"type": "boolean", "description": "用扫描码而不是虚拟键码"},
        },
    ),
    _t(
        "mouse",
        "鼠标滑动 / 拖拽 / 滚轮。默认插值成一串 WM_MOUSEMOVE 后台投递，不动光标。",
        _TARGET_PROPS,
        extra={
            "mode": {"type": "string", "enum": ["swipe", "drag", "wheel"]},
            "path": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}},
                     "description": "途经点 [[x,y],[x,y]...]，客户区坐标"},
            "from": {"type": "array", "items": {"type": "integer"}},
            "to": {"type": "array", "items": {"type": "integer"}},
            "delta": {"type": "integer", "description": "滚轮格数，正上负下"},
            "method": {"type": "string", "enum": ["post", "send", "sendinput", "hardware"]},
        },
    ),
    _t(
        "uia_tree",
        "用 UI Automation 读窗口里的控件树。按名字/类型查元素并拿到屏幕坐标 —— "
        "比截图数像素稳得多。不传筛选条件则返回整棵树。",
        _TARGET_PROPS,
        extra={
            "name": {"type": "string", "description": "按元素名筛选"},
            "control_type": {"type": "string", "description": "按控件类型，如 button / edit"},
            "automation_id": {"type": "string"},
            "class_name": {"type": "string"},
            "name_exact": {"type": "boolean", "description": "名字要求完全相等"},
            "interactive_only": {"type": "boolean", "description": "只返回可交互元素"},
            "max_depth": {"type": "integer", "description": "遍历深度上限，默认 12"},
            "limit": {"type": "integer", "description": "元素数量上限，默认 500"},
        },
    ),
]

# 工具名 -> (HTTP 方法, 路径)
_ROUTES = {
    "window_info": ("POST", "/window"),
    "probe": ("POST", "/probe"),
    "click": ("POST", "/click"),
    "screenshot": ("POST", "/screenshot"),
    "type_text": ("POST", "/text"),
    "send_key": ("POST", "/key"),
    "mouse": ("POST", "/mouse"),
    "uia_tree": ("POST", "/uia"),
}


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _text_result(payload: Any, is_error: bool = False) -> dict:
    if isinstance(payload, str):
        body = payload
    else:
        body = json.dumps(payload, ensure_ascii=False, indent=2)
    # 太长的结果截断，省 token —— agent 拿到 500 个元素的全量 JSON 也没法用
    if len(body) > 60000:
        body = body[:60000] + "\n... [结果过长已截断，请用 limit / 筛选条件缩小范围]"
    return {"content": [{"type": "text", "text": body}], "isError": is_error}


class McpServer:
    def __init__(self) -> None:
        self._client: Optional[Client] = None
        self._client_port: Optional[int] = None

    # --- 服务连接 ---

    def client(self) -> Client:
        """拿一个可用客户端；服务没起就拉起来。"""
        if self._client is not None:
            return self._client
        info = ensure_server()          # 内部会探活，必要时自启并等待
        port = info.get("port") if isinstance(info, dict) else None
        self._client = Client(port=port)
        self._client_port = port
        return self._client

    def _reset_client(self) -> None:
        self._client = None
        self._client_port = None

    # --- 工具实现 ---

    def _tool_list_windows(self, args: dict) -> dict:
        data = self.client().windows()
        rows = []
        needle = (args.get("filter") or "").lower()
        only_postable = bool(args.get("only_postable"))
        for w in data.get("windows", []):
            if only_postable and not w.get("postable"):
                continue
            hay = f"{w.get('title','')} {w.get('process','')}".lower()
            if needle and needle not in hay:
                continue
            rows.append({
                "hwnd": w.get("hwnd_hex"),
                "process": w.get("process"),
                "title": w.get("title"),
                "client_size": w.get("client_size"),
                "postable": w.get("postable"),
            })
        return {"ok": True, "count": len(rows), "windows": rows,
                "hint": "用 hwnd 指定目标；postable=false 的窗口后台投递会被 UIPI 拦，"
                        "改用 method=hardware 或 sendinput"}

    def _tool_http(self, name: str, args: dict) -> dict:
        method, path = _ROUTES[name]
        c = self.client()
        try:
            return c._request(method, path, args)  # noqa: SLF001
        except ConnectionError:
            # 服务中途挂了（重启过 / 崩了），重连一次
            self._reset_client()
            return self.client()._request(method, path, args)  # noqa: SLF001

    def call_tool(self, name: str, args: dict) -> dict:
        if name == "list_windows":
            return _text_result(self._tool_list_windows(args))
        if name not in _ROUTES:
            return _text_result(f"未知工具：{name}", is_error=True)

        payload = self._tool_http(name, args)

        # 截图内联：直接回图片内容，模型能看见
        if name == "screenshot" and isinstance(payload, dict):
            shot = payload.get("screenshot") or {}
            b64 = shot.get("base64")
            if b64:
                return {
                    "content": [
                        {"type": "text",
                         "text": f"截图已保存：{shot.get('path')}  "
                                 f"{shot.get('width')}x{shot.get('height')}"},
                        {"type": "image", "data": b64, "mimeType": "image/png"},
                    ],
                    "isError": False,
                }

        ok = payload.get("ok", True) if isinstance(payload, dict) else True
        return _text_result(payload, is_error=not ok)

    # --- JSON-RPC ---

    def handle(self, msg: dict) -> Optional[dict]:
        method = msg.get("method")
        rid = msg.get("id")
        params = msg.get("params") or {}

        # 通知（没有 id）不需要回包
        if rid is None and isinstance(method, str) and method.startswith("notifications/"):
            return None

        if method == "initialize":
            want = params.get("protocolVersion")
            return self._ok(rid, {
                "protocolVersion": want if want in KNOWN_PROTOCOLS else DEFAULT_PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
                "instructions": (
                    "操作 Windows 窗口的后台自动化工具。默认不抢光标和焦点。"
                    "先 list_windows 拿 hwnd，再 click / type_text / screenshot。"
                    "若窗口 postable=false，改用 method=hardware 或 sendinput。"
                ),
            })

        if method == "ping":
            return self._ok(rid, {})

        if method == "tools/list":
            return self._ok(rid, {"tools": TOOLS})

        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            if not isinstance(args, dict):
                return self._err(rid, -32602, "arguments 必须是对象")
            try:
                return self._ok(rid, self.call_tool(name, args))
            except Exception as e:
                return self._ok(rid, _text_result(
                    f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1200:]}",
                    is_error=True,
                ))

        if method in ("resources/list", "prompts/list"):
            # 明确告知没有这些能力，客户端就不会再问
            return self._err(rid, -32601, f"不支持 {method}")

        return self._err(rid, -32601, f"未知方法 {method}")

    @staticmethod
    def _ok(rid: Any, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    @staticmethod
    def _err(rid: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def serve(stdin=None, stdout=None) -> int:
    # stdout 是协议通道。真跑 stdio 时先把它抢过来，再把 sys.stdout 指到 stderr，
    # 这样任何库里的 print 都不会污染协议流。
    # 传了 stdout（测试场景）就不动全局状态，免得把调用方的输出抢走。
    if stdout is None:
        out = sys.stdout
        try:
            out.reconfigure(encoding="utf-8", newline="\n")  # type: ignore[attr-defined]
        except Exception:
            pass
        sys.stdout = sys.stderr
    else:
        out = stdout
        try:
            out.reconfigure(encoding="utf-8", newline="\n")  # type: ignore[attr-defined]
        except Exception:
            pass

    inp = stdin or sys.stdin
    try:
        inp.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    server = McpServer()
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception as e:
            out.write(_dumps({"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32700, "message": f"JSON 解析失败: {e}"}}) + "\n")
            out.flush()
            continue

        if isinstance(msg, list):      # 批量请求
            replies = [r for r in (server.handle(m) for m in msg) if r is not None]
            if replies:
                out.write(_dumps(replies) + "\n")
        else:
            reply = server.handle(msg)
            if reply is not None:
                out.write(_dumps(reply) + "\n")
        out.flush()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog="bgmcp",
        description="bgclick 的 MCP server（stdio）。给 Claude Desktop / Cursor 等客户端用。",
    )
    p.add_argument("--version", action="version", version=f"bgmcp {__version__}")
    p.parse_args(argv)
    try:
        return serve()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
