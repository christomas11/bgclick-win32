# -*- coding: utf-8 -*-
"""bgkit.bgclient.cli.py —— 命令行：参数、目标解析、输出与退出码

从 bgclient.py 拆出的一节（源文件第 307-713 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from .bootstrap import _ping, ensure_server
from .http import Client
from .paths import DEFAULT_HOST, read_port



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

    sp = add("key", "键盘按键 / 组合键 / 多键同按（走 WM_KEYDOWN，不是 WM_CHAR）")
    add_target_args(sp)
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--keys", action="append", metavar="CHORD",
                   help="组合键，可重复给（如 --keys ctrl+shift+s --keys f5）。"
                        "解析：ctrl / shift / alt / win + 主键")
    g.add_argument("--hold", metavar="KEYS",
                   help="多键同按并按住不放，如 --hold ctrl+shift+a（逗号或加号分隔，"
                        "也支持 ctrl,shift,a）")
    g.add_argument("--type", dest="typed", metavar="TEXT",
                   help="逐字符真按键输入（每个字符拆成 修饰键+主键，再补 WM_CHAR）")
    sp.add_argument("--repeat", type=int, default=1, help="重复次数（默认 1）")
    sp.add_argument("--interval", type=float, default=0.06,
                    help="两次组合之间的间隔秒（默认 0.06；--type 为 0.01）")
    sp.add_argument("--hold-gap", type=float, default=0.02,
                    help="修饰键与主键之间的间隔秒（默认 0.02）")
    sp.add_argument("--hold-seconds", type=float, default=0.5,
                    help="--hold 的按住时长秒（默认 0.5）")
    sp.add_argument("--method", default="post", choices=["post", "send", "sendinput"],
                    help="投递方式：post 默认（后台）/ send 同步 / "
                         "sendinput 系统输入队列 + 扫描码（只认底层键盘输入的程序吃这个）")
    sp.add_argument("--no-scancode", dest="scancode", action="store_false", default=True,
                    help="sendinput 时改用虚拟键码而不是扫描码")
    sp.add_argument("--no-char", dest="char", action="store_false", default=None,
                    help="不补 WM_CHAR，只发按键消息")
    sp.add_argument("--with-char", dest="char", action="store_true", default=None,
                    help="强制补 WM_CHAR（Ctrl/Alt 组合默认是不补的）")
    sp.add_argument("--as-keys", dest="text_mode", action="store_const", const="keys",
                    help="--type 时全部走按键消息，不发 WM_CHAR（给 IDE/游戏/画布）")
    sp.add_argument("--as-both", dest="text_mode", action="store_const", const="both",
                    help="--type 时两条通道都发（老行为，记事本这类控件会字符翻倍）")

    sp = add("mouse", "鼠标滑动 / 拖拽 / 滚轮（走消息投递，不动真实光标）")
    add_target_args(sp)
    sp.add_argument("--from", dest="from_pos", type=parse_pair,
                    help="起点客户区坐标 X,Y（默认客户区中心）")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--delta", type=parse_pair, metavar="DX,DY",
                   help="相对滑动，如 --delta 0,-500（向上滑 500 像素）")
    g.add_argument("--to", dest="to_pos", type=parse_pair, help="滑到绝对客户区坐标 X,Y")
    g.add_argument("--path", help='途经点，形如 "100,100;300,300;500,200"')
    g.add_argument("--pattern", choices=["line", "up", "down", "left", "right",
                                         "circle", "square", "zigzag"],
                   help="手势轨迹（圆/方/锯齿默认绕回起点）")
    g.add_argument("--scroll", type=int, help="滚轮格数（正数向上/向左，负数向下/向右）")
    sp.add_argument("--axis", default="vertical", choices=["vertical", "horizontal"],
                    help="滚轮方向轴（配合 --scroll）")
    sp.add_argument("--drag", action="store_true", help="按住拖动（起点按下，全程保持）")
    sp.add_argument("--button", default="left", choices=["left", "right", "middle"],
                    help="拖拽用哪个键（默认 left）")
    sp.add_argument("--distance", type=int, default=300, help="手势轨迹尺度（像素，默认 300）")
    sp.add_argument("--steps", type=int, default=30, help="插值步数（默认 30）")
    sp.add_argument("--duration", type=float, help="整段滑动的总时长秒")
    sp.add_argument("--delay", type=float, default=0.02, help="每步间隔秒（默认 0.02）")
    sp.add_argument("--hold", type=float, default=0.03, help="按下/抬起前停顿秒")
    sp.add_argument("--ease", action="store_true", help="平滑加减速，更像人手")
    sp.add_argument("--no-release", dest="release", action="store_false", default=True,
                    help="拖完不抬键（慎用）")
    sp.add_argument("--method", default="post",
                    choices=["post", "send", "hardware", "sendinput"],
                    help="投递方式：post 纯后台（默认）/ send 同步 / "
                         "hardware 真输入（SetCursorPos+mouse_event）/ "
                         "sendinput 系统输入队列（整批原子提交，拖拽最稳）")
    sp.add_argument("--no-batch", dest="batch", action="store_false", default=True,
                    help="sendinput 模式下逐点提交而不是整批（轨迹在时间上更像人手）")
    sp.add_argument("--no-restore-cursor", dest="restore_cursor", action="store_false",
                    default=True, help="hardware/sendinput 模式结束后不把光标放回原处")

    sp = add("uia", "UIA 元素查询（按名字/类型找元素并拿坐标）")
    add_target_args(sp)
    sp.add_argument("--name", help="元素名（默认子串匹配，不区分大小写）")
    sp.add_argument("--type", help="控件类型，如 Button / Edit / MenuItem")
    sp.add_argument("--automation-id", help="按 AutomationId 匹配（子串）")
    sp.add_argument("--class-name", help="按 ClassName 匹配（子串）")
    # 注意：不能用 --exact —— add_target_args 已经把它注册成「标题完全匹配」了，
    # 重复注册会让 argparse 抛 conflicting option string。所以这里另起一个名字。
    sp.add_argument("--name-exact", action="store_true",
                    help="--name 改为完全匹配（区别于 --exact，后者管的是标题）")
    sp.add_argument("--interactive-only", action="store_true",
                    help="只保留疑似可交互的元素")
    sp.add_argument("--max-depth", type=int, default=12, help="遍历深度上限（默认 12）")
    sp.add_argument("--limit", type=int, default=500,
                    help="最多采集多少元素（默认 500；超了返回部分结果并标注截断）")

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

        if args.cmd == "key":
            body = target_payload(args)
            body.update({"repeat": args.repeat, "method": args.method,
                         "hold_gap": args.hold_gap, "interval": args.interval,
                         "scancode": args.scancode})
            if args.char is not None:
                body["char"] = args.char
            human = None
            if args.keys:
                body["chords"] = args.keys
            elif args.hold:
                # 同时接受 ctrl+shift+a 和 ctrl,shift,a 两种分隔写法
                body["keys"] = [t for t in re.split(r"[+,]", args.hold) if t.strip()]
                body["hold"] = True
                body["hold_seconds"] = args.hold_seconds
            else:
                body["text"] = args.typed
                body["interval"] = 0.01 if args.interval == 0.06 else args.interval
                if args.text_mode:
                    body["mode"] = args.text_mode
            r = client.key(**body)
            if r.get("ok"):
                k = r.get("key", {})
                what = args.hold or ("+".join(args.keys) if args.keys else args.typed)
                human = (f"已投递 {k.get('sent')} 条键盘消息（{what}，"
                         f"目标 {k.get('title')}，{k.get('elapsed')}s）")
            return emit(r, as_json, human)

        if args.cmd == "mouse":
            body = target_payload(args)
            body.update({"method": args.method, "steps": args.steps, "hold": args.hold,
                         "ease": args.ease, "distance": args.distance,
                         "release": args.release, "batch": args.batch,
                         "restore_cursor": args.restore_cursor})
            if args.delay != 0.02:
                body["delay"] = args.delay
            if args.from_pos:
                body["from"] = args.from_pos
            if args.duration is not None:
                body["duration"] = args.duration
            if args.scroll is not None:
                body["scroll"] = args.scroll
                body["axis"] = args.axis
            elif args.pattern:
                body["pattern"] = args.pattern
            elif args.path:
                pts = []
                for chunk in args.path.replace("，", ",").split(";"):
                    chunk = chunk.strip()
                    if not chunk:
                        continue
                    try:
                        a, b = chunk.split(",")
                        pts.append([int(a.strip(), 0), int(b.strip(), 0)])
                    except Exception:
                        raise SystemExit(
                            f"--path 格式应为 \"x,y;x,y\"，收到 {chunk!r}")
                body["path"] = pts
            elif args.to_pos:
                body["to"] = args.to_pos
            else:
                body["dx"], body["dy"] = args.delta
            if args.drag:
                body["button"] = args.button
            elif args.button != "left":
                body["button"] = args.button
            r = client.mouse(**body)
            human = None
            if r.get("ok"):
                m = r.get("mouse", {})
                if m.get("mode") == "scroll":
                    human = (f"已滚 {m.get('amount')} 格（{m.get('axis')}），"
                             f"投递 {m.get('sent')} 条消息（目标 {m.get('title')}）")
                else:
                    human = (f"{'拖拽' if m.get('mode') == 'drag' else '滑动'} "
                             f"{m.get('from')} → {m.get('to')}，"
                             f"{m.get('steps')} 步 / {m.get('sent')} 条消息"
                             f"（目标 {m.get('title')}）")
            return emit(r, as_json, human)

        if args.cmd == "uia":
            body = target_payload(args)
            body.update({"max_depth": args.max_depth, "limit": args.limit})
            if args.name:
                body["name"] = args.name
            if args.type:
                body["control_type"] = args.type
            if args.automation_id:
                body["automation_id"] = args.automation_id
            if args.class_name:
                body["class_name"] = args.class_name
            if args.name_exact:
                body["name_exact"] = True
            if args.interactive_only:
                body["interactive_only"] = True
            r = client.uia(**body)
            human = None
            if r.get("ok"):
                u = r.get("uia", {})
                lines = [f"读到 {u.get('count', 0)} 个元素"
                         f"（visited={u.get('visited')}，最深 {u.get('max_depth')} 层，"
                         f"{u.get('elapsed')}s）"]
                if u.get("truncated"):
                    lines.append(f"★ 已达采集上限 {u.get('limit')}，结果是部分内容")
                if u.get("failed_reason"):
                    lines.append(f"注意：{u['failed_reason']}")
                els = u.get("elements", [])
                for i, e in enumerate(els[:60]):
                    lines.append(
                        f"  [{i}] {str(e.get('control_type', '')).lower():<14}"
                        f"屏幕 {str(e.get('center')):<14}"
                        f"客户区 {str(e.get('client_center')):<14}"
                        f"\"{str(e.get('name', ''))[:40]}\""
                        + ("  [可交互]" if e.get("is_interactive") else "")
                    )
                if len(els) > 60:
                    lines.append(f"  ... 还有 {len(els) - 60} 个，"
                                 f"加 --type / --name 缩小范围")
                human = "\n".join(lines)
            return emit(r, as_json, human)

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
