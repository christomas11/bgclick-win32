"""CI 用：验证装好之后 `python -m bgkit.bgmcp` 能完成 MCP 握手并列出工具。

两个曾经踩过的坑，写在这里免得以后有人改回去：

1. 不要写成 workflow 里的内联 `python -c`。GitHub Actions 的 windows runner
   会先用一层 shell 包装命令，内联代码里的转义双引号和换行符会被这层
   shell 的引号解析吃掉，Python 拿到的是被截断的字符串，直接
   SyntaxError: unterminated string literal。

2. 这个脚本里不要出现非 ASCII 字符。runner 的控制台编码是 cp1252，
   print 中文会抛 UnicodeEncodeError: 'charmap' codec can't encode。
   所以下面所有输出都是英文，并且开头会把 stdout 尽量切到 UTF-8 兜底。

注意：文件名不是 test_*.py，pytest 不会收集它，只是给 CI 调用的脚本。
"""
import json
import subprocess
import sys

# 工具表里必须有这些 —— 少一个说明转发路由断了
REQUIRED_TOOLS = ("click", "uia_tree", "wait_for", "screenshot", "list_windows")
MIN_TOOLS = 10


def _force_utf8_stdout() -> None:
    """runner 的控制台可能是 cp1252，尽量切到 UTF-8；切不动就算了。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


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
        print("[X] bgmcp exited with code %s" % proc.returncode)
        print("--- stderr ---")
        print((proc.stderr or "")[:2000])
        return 1

    raw = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    if not raw:
        print("[X] stdout is empty - MCP sent no reply")
        print("--- stderr ---")
        print((proc.stderr or "")[:2000])
        return 1

    replies = {}
    for line in raw:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            # 协议流里混进一行非 JSON，客户端会直接断连，必须当失败
            print("[X] non-JSON line leaked into the protocol stream: %r" % line[:200])
            return 1
        if "id" in msg:
            replies[msg["id"]] = msg

    info = (replies.get(1, {}).get("result") or {}).get("serverInfo") or {}
    print("serverInfo: %s" % (info,))
    if info.get("name") != "bgclick":
        print("[X] serverInfo.name is not bgclick")
        return 1

    tools = ((replies.get(2, {}).get("result") or {}).get("tools")) or []
    names = [t.get("name") for t in tools]
    print("tools: %d -> %s" % (len(tools), ", ".join(n for n in names if n)))

    if len(tools) < MIN_TOOLS:
        print("[X] expected at least %d tools" % MIN_TOOLS)
        return 1
    missing = [n for n in REQUIRED_TOOLS if n not in names]
    if missing:
        print("[X] missing tools: %s" % ", ".join(missing))
        return 1

    print("[OK] MCP handshake passed with %d tools" % len(tools))
    return 0


if __name__ == "__main__":
    _force_utf8_stdout()
    sys.exit(main())
