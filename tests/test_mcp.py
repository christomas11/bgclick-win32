# -*- coding: utf-8 -*-
"""MCP 协议测试 —— 只验协议层，不起服务、不碰窗口。

MCP 客户端对协议非常挑剔：initialize 回错版本、tools/list 少字段、
通知被回了包，都会让连接直接断掉，而且客户端通常只报一句
「server disconnected」，查起来极其痛苦。所以这几条必须有测试兜底。
"""

from __future__ import annotations

import io
import json

from bgkit.bgmcp.server import DEFAULT_PROTOCOL, KNOWN_PROTOCOLS, TOOLS, serve


def roundtrip(messages: list[dict]) -> list[dict]:
    """把请求喂给 serve()，收回来的响应解析成 list。"""
    payload = "\n".join(json.dumps(m, ensure_ascii=False) for m in messages) + "\n"
    out = io.StringIO()
    serve(stdin=io.StringIO(payload), stdout=out)
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def test_initialize_returns_server_info():
    [resp] = roundtrip([{
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": DEFAULT_PROTOCOL,
                   "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
    }])
    result = resp["result"]
    assert result["serverInfo"]["name"] == "bgclick"
    assert "tools" in result["capabilities"]
    assert result["protocolVersion"] in KNOWN_PROTOCOLS


def test_initialize_echoes_known_protocol():
    for ver in KNOWN_PROTOCOLS:
        [resp] = roundtrip([{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": ver}}])
        assert resp["result"]["protocolVersion"] == ver


def test_initialize_falls_back_on_unknown_protocol():
    [resp] = roundtrip([{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": "1999-01-01"}}])
    assert resp["result"]["protocolVersion"] == DEFAULT_PROTOCOL


def test_notification_gets_no_reply():
    """notifications/initialized 不该有响应 —— 回了包客户端会认为协议乱套。"""
    replies = roundtrip([
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ])
    assert replies == []


def test_tools_list_shape():
    [resp] = roundtrip([{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    tools = resp["result"]["tools"]
    assert len(tools) == len(TOOLS)
    for t in tools:
        assert set(t) >= {"name", "description", "inputSchema"}


def test_ping():
    [resp] = roundtrip([{"jsonrpc": "2.0", "id": 3, "method": "ping"}])
    assert resp["result"] == {}


def test_unknown_method_returns_error():
    [resp] = roundtrip([{"jsonrpc": "2.0", "id": 4, "method": "no/such"}])
    assert resp["error"]["code"] == -32601


def test_malformed_json_returns_parse_error():
    out = io.StringIO()
    serve(stdin=io.StringIO("{这不是 json}\n"), stdout=out)
    resp = json.loads(out.getvalue().strip())
    assert resp["error"]["code"] == -32700


def test_stdout_stays_clean_when_handling():
    """协议流里只能有 JSON，一行都不能多。"""
    out = io.StringIO()
    serve(stdin=io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"
    ), stdout=out)
    for line in out.getvalue().splitlines():
        json.loads(line)   # 解析不了就说明有脏东西
