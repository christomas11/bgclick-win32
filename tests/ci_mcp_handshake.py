"""CI 用：验证装好之后 `python -m bgkit.bgmcp` 能完成 MCP 握手并列出工具。

为什么不直接写在 workflow 里用 `python -c`：GitHub Actions 的 windows runner
用 pwsh 执行 run 步骤（命令是 `pwsh -command ". '{0}'"` 包一层），内联代码里的
`\"` 和 `\n` 会被 PowerShell 的引号解析吃掉，Python 拿到的是被截断的字符串，
直接 SyntaxError: unterminated string literal。实测 3.8 / 3.12 都栽在这一步。

放成独立文件就没这问题，顺便能把校验写清楚。

注意：文件名不是 test_*.py，pytest 不会收集它，只是给 CI 调用的脚本。
"""
import json
import subprocess
import sys

# 工具表里必须有这些 —— 少一个说明转发路由断了
REQUIRED_TOOLS = ("click", "uia_tree", "wait_for", "screenshot", "list_windows")
MIN_TOOLS = 10


def main() -> int:
    requests = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05",
                               "capabilities": {},
                               "clientInfo": {"name": "ci", "version": "0"}}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    ]
    payload = "\n".join(requests) + "\n"

    proc = subprocess.run(
        [sys.executable, "-m", "bgkit.bgmcp"],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )

    if proc.returncode != 0:
        print("[X] bgmcp 退出码 %s" % proc.returncode)
        print("--- stderr ---")
        print((proc.stderr or "")[:2000])
        return 1

    raw = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    if not raw:
        print("[X] stdout 是空的，MCP 没有回包")
        print("--- stderr ---")
        print((proc.stderr or "")[:2000])
        return 1

    replies = {}
    for line in raw:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            # 协议流里混进一行非 JSON，客户端会直接断连，必须当失败
            print("[X] 协议流里混入了非 JSON 内容: %r" % line[:200])
            return 1
        if "id" in msg:
            replies[msg["id"]] = msg

    info = (replies.get(1, {}).get("result") or {}).get("serverInfo") or {}
    print("serverInfo: %s" % (info,))
    if info.get("name") != "bgclick":
        print("[X] 服务名不是 bgclick")
        return 1

    tools = ((replies.get(2, {}).get("result") or {}).get("tools")) or []
    names = [t.get("name") for t in tools]
    print("工具 %d 个: %s" % (len(tools), ", ".join(n for n in names if n)))

    if len(tools) < MIN_TOOLS:
        print("[X] 工具数量不足，期望 >= %d" % MIN_TOOLS)
        return 1
    missing = [n for n in REQUIRED_TOOLS if n not in names]
    if missing:
        print("[X] 缺少工具: %s" % ", ".join(missing))
        return 1

    print("[OK] MCP 握手通过，%d 个工具" % len(tools))
    return 0


if __name__ == "__main__":
    sys.exit(main())
