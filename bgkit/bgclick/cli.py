# -*- coding: utf-8 -*-
"""bgkit.bgclick.cli.py —— 命令行：参数、诊断子命令与入口

从 bgclick.py 拆出的一节（源文件第 2720-3575 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import random
import sys
import time
from typing import Optional
from .clicking import do_click, resolving_click
from .elevation import elevation_would_help, relaunch_as_admin
from .errors import AppError, describe_error
from .geometry import deepest_child_at, resolve_client_point
from .integrity import integrity_name, process_integrity, self_integrity
from .keys import parse_pair
from .messaging import probe_hierarchy, probe_window
from .mouse import do_mouse
from .screen import save_screenshot
from .targeting import match_windows
from .win32 import BUTTONS, ERROR_ACCESS_DENIED, user32
from .wininfo import describe, enable_dpi_awareness, is_admin, iter_top_level_windows, process_is_elevated, process_name, window_class, window_pid, window_title



# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bgclick.py",
        description="对指定窗口后台点击（PostMessage 投递，不抢光标/焦点）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--list", action="store_true", help="列出可见窗口后退出")
    p.add_argument("--list-all", action="store_true", help="列出全部顶层窗口（含隐藏）后退出")
    p.add_argument("--doctor", action="store_true", help="只读诊断：为什么点不动")
    p.add_argument("--doctor-click", action="store_true",
                   help="诊断时额外真投递一次（会真的点目标一下，慎用）")
    p.add_argument("--scan", action="store_true",
                   help="扫描所有窗口，列出哪些可投递、哪些被 UIPI 拦截")
    p.add_argument("--title", help="窗口标题（默认子串匹配，忽略大小写）")
    p.add_argument("--regex", action="store_true", help="标题按正则匹配")
    p.add_argument("--exact", action="store_true", help="标题必须完全相等")
    p.add_argument("--process", help="同时要求进程名，如 notepad.exe")
    p.add_argument("--hwnd", type=lambda s: int(s, 0), help="直接指定窗口句柄")
    p.add_argument("--index", type=int, default=0, help="匹配到多个窗口时选第几个（默认 0）")
    p.add_argument("--only-visible", action="store_true", default=True,
                   help="只匹配可见窗口（默认开）")
    p.add_argument("--include-hidden", dest="only_visible", action="store_false",
                   help="把隐藏窗口也纳入匹配")

    g = p.add_mutually_exclusive_group()
    g.add_argument("--pos", type=parse_pair, help="客户区偏移坐标 X,Y（可带 0x 前缀）")
    g.add_argument("--center", action="store_true", help="点客户区正中心")
    g.add_argument("--screen-pos", type=parse_pair, help="屏幕绝对坐标 X,Y")

    p.add_argument("--button", choices=list(BUTTONS), default="left", help="鼠标键（默认 left）")
    p.add_argument("--count", type=int, default=1, help="点击次数，0 = 无限循环（默认 1）")
    p.add_argument("--interval", type=float, default=0.5, help="间隔秒数（默认 0.5）")
    p.add_argument("--jitter", type=int, default=0, help="每次点击的随机像素抖动（默认 0）")
    p.add_argument("--method", choices=["post", "send", "hardware", "sendinput"],
                   default="post",
                   help="投递方式（post 默认纯后台 / send 同步 / hardware 真输入 / "
                        "sendinput 系统输入队列+扫描码）")
    p.add_argument("--no-deep", dest="deep", action="store_false", default=True,
                   help="不向下钻取子窗口，直接发给顶层窗口")
    p.add_argument("--restore-cursor", dest="restore_cursor", action="store_true",
                   default=True, help="hardware 模式下结束后把光标放回原处（默认就放回）")
    p.add_argument("--no-restore-cursor", dest="restore_cursor", action="store_false",
                   help="hardware 模式下不恢复光标（留在原地）")
    p.add_argument("--activate", action="store_true", help="点击前先把窗口调到前台")
    p.add_argument("--elevate", action="store_true",
                   help="立刻申请管理员权限重跑（不等检测，会弹 UAC）")
    p.add_argument("--no-elevate", dest="auto_elevate", action="store_false", default=True,
                   help="禁止自动提权（默认：检测到权限不够就自己弹 UAC）")
    p.add_argument("--_elevated", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--force-continue", action="store_true",
                   help="即使投递失败也继续循环（默认失败即停）")
    p.add_argument("--shot", help="截图并存到指定路径（PNG；后缀 .bmp 则存 BMP）")
    p.add_argument("--client-only", action="store_true",
                   help="只截客户区，不含标题栏和边框")
    p.add_argument("--shot-method", choices=["auto", "print", "bitblt"], default="auto",
                   help="截图方式（默认 auto：PrintWindow 优先，可抓被遮挡的窗口）")
    p.add_argument("--shot-dir", default="shots",
                   help="截图默认输出目录（默认 ./shots）")
    p.add_argument("--dry-run", action="store_true", help="只打印坐标，不发消息")
    p.add_argument("--json", action="store_true", help="JSON 输出")
    p.add_argument("--keep-open", action="store_true", help="结束时等回车")

    # ---- 滑动 / 拖拽 / 滚轮（不加 --swipe 时这些参数不生效）----
    p.add_argument("--swipe", action="store_true",
                   help="滑动手势模式：用 --dx/--dy 或 --to / --path / --pattern 指定轨迹")
    p.add_argument("--from", dest="from_pos", type=parse_pair,
                   help="滑动起点客户区坐标 X,Y（默认客户区中心）")
    p.add_argument("--dx", type=int, default=0, help="水平位移（正数向右）")
    p.add_argument("--dy", type=int, default=0, help="垂直位移（正数向下，负数向上）")
    p.add_argument("--to", dest="to_pos", type=parse_pair, help="滑到绝对客户区坐标 X,Y")
    p.add_argument("--path", help='途经点，形如 "100,100;300,300;500,200"')
    p.add_argument("--pattern", choices=["line", "up", "down", "left", "right",
                                         "circle", "square", "zigzag"],
                   help="手势轨迹（圆/方/锯齿默认会绕回起点，可用 --no-to-start 关掉）")
    p.add_argument("--distance", type=int, default=300, help="手势轨迹的尺度（像素，默认 300）")
    p.add_argument("--points", type=int, default=None, help="手势采样点数（圆默认 72）")
    p.add_argument("--steps", type=int, default=30, help="插值步数（默认 30，越多越平滑）")
    p.add_argument("--duration", type=float, default=None,
                   help="整段滑动的总时长秒（给了就按它反推每步间隔）")
    p.add_argument("--delay", type=float, default=0.02, help="每步间隔秒（默认 0.02）")
    p.add_argument("--hold", type=float, default=0.03, help="按下/抬起前的停顿秒（拖拽用）")
    p.add_argument("--ease", action="store_true", help="平滑加减速（起步慢收尾慢，更像人手）")
    p.add_argument("--drag", action="store_true", help="按住并拖动（配合 --dx/--dy/--to/--path）")
    p.add_argument("--release", action="store_true", default=True, help="结束时抬起（默认就是抬）")
    p.add_argument("--no-release", dest="release", action="store_false",
                   help="结束时不抬键（留一个按住状态，慎用）")
    p.add_argument("--no-to-start", dest="to_start", action="store_false", default=True,
                   help="手势走完不回到起点")
    # 注意：--no-deep 在上面（点击那组参数里）已经注册过，这里不能重复注册，
    # 否则 argparse 直接抛 "conflicting option string" —— 滑动复用同一个开关。
    p.add_argument("--scroll", type=int, default=None,
                   help="滚轮格数（正数向上/向左，负数向下/向右）")
    p.add_argument("--axis", default="vertical", choices=["vertical", "horizontal"],
                   help="滚轮方向轴")
    return p


def cmd_list(args) -> int:
    rows = []
    for hwnd in iter_top_level_windows():
        if not args.list_all and not user32.IsWindowVisible(hwnd):
            continue
        title = window_title(hwnd)
        if not args.list_all and not title:
            continue
        rows.append(describe(hwnd))
    rows.sort(key=lambda r: (not r["visible"], r["process"], r["title"]))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    me = "管理员" if is_admin() else "普通用户"
    print(f"共 {len(rows)} 个窗口（本进程权限：{me}）\n")
    print(f"{'句柄(hex)':<14}{'管理员':<8}{'进程':<24}{'客户区':<12}标题")
    print("-" * 104)
    for r in rows:
        cw, ch = r["client_size"]
        elev = process_is_elevated(r["pid"])
        flag = "是" if elev else ("否" if elev is False else "?")
        print(f"{r['hwnd_hex']:<14}{flag:<8}{r['process'][:22]:<24}"
              f"{str(cw) + 'x' + str(ch):<12}{r['title'][:46]}")
    return 0


def cmd_doctor(args) -> int:
    """只读诊断：把「为什么点不动」直接照出来。"""
    lines: list[str] = []

    def say(s=""):
        lines.append(s)

    say("=" * 68)
    say("bgclick 诊断报告")
    say("=" * 68)
    admin = is_admin()
    self_il = self_integrity()
    say(f"[1] 本进程权限       : {'管理员 (已提权)' if admin else '普通用户 (未提权)'}")
    say(f"    完整性级别(IL)   : {integrity_name(self_il)}"
        + ("   ★ 受限沙箱！" if self_il is not None and self_il < 0x2000 else ""))
    if self_il is not None and self_il < 0x2000:
        say("    警告             : IL 低于 Medium，按 UIPI 规则**发不进任何普通程序**。")
        say("                       这通常是沙箱/受限环境导致的，跟目标程序无关。")
        say("                       请在普通 PowerShell（或提权窗口）里跑本脚本。")
    say(f"    DPI 感知         : {enable_dpi_awareness() or '未设置（坐标可能有偏差）'}")
    say(f"    Python           : {sys.version.split()[0]}  {8 * ctypes.sizeof(ctypes.c_void_p)} 位")

    if not args.title and args.hwnd is None and not args.process:
        say("")
        say("没给 --title / --hwnd / --process，只能报告本进程权限。")
        say("补上目标窗口信息再跑一次，比如：")
        say('  python bgclick.py --doctor --title "xxx" --pos 400,300')
        print("\n".join(lines))
        return 0

    cands = match_windows(args)
    if not cands:
        say("")
        say("[!] 没找到匹配的窗口。先跑 --list 确认标题。")
        print("\n".join(lines))
        return 1

    say("")
    say(f"[2] 匹配到 {len(cands)} 个窗口" + (f"，本次分析 --index {args.index}" if len(cands) > 1 else ""))
    for i, c in enumerate(cands[:8]):
        say(f"    {'*' if i == args.index else ' '}[{i}] {c['hwnd_hex']:<12}"
            f"{c['process'][:20]:<22}{c['client_size'][0]}x{c['client_size'][1]:<8}{c['title'][:36]}")

    if args.index >= len(cands):
        say(f"[!] --index {args.index} 越界")
        print("\n".join(lines))
        return 2

    t = cands[args.index]
    hwnd = t["hwnd"]
    elev = process_is_elevated(t["pid"])
    tgt_il = process_integrity(t["pid"])
    say("")
    say(f"[3] 目标进程权限     : "
        + ("管理员 (已提权)" if elev else "普通用户" if elev is False else "读不到（可能是受保护进程）"))
    say(f"    目标完整性级别   : {integrity_name(tgt_il)}")
    say(f"    窗口类名         : {t['class']}")

    # UIPI 是「单向过滤器」：IL(自己) >= IL(目标) 才发得进去
    verdict_permission = None
    if self_il is not None and tgt_il is not None:
        if self_il < tgt_il:
            verdict_permission = (
                f"UIPI 必定拦截：本进程 IL={integrity_name(self_il)} < "
                f"目标 IL={integrity_name(tgt_il)}。"
                + ("本进程在受限沙箱里，请换普通终端跑。" if self_il < 0x2000 else "需要 --elevate。")
            )
        elif self_il >= tgt_il:
            verdict_permission = None  # 权限 OK
    elif elev is True and not admin:
        verdict_permission = ("目标是管理员，本进程是普通用户。→ 必须用 --elevate")
    elif elev is None and not admin:
        verdict_permission = "读不到目标完整性级别，无法排除 UIPI。若点不动就试 --elevate"

    # 坐标解析
    cw, ch = t["client_size"]
    point = None
    if args.center:
        point = (cw // 2, ch // 2)
    elif args.pos:
        point = args.pos
    elif args.screen_pos:
        point = resolve_client_point(hwnd, *args.screen_pos)
    say("")
    if point is None:
        say("[4] 坐标             : 未指定（--pos / --center / --screen-pos 都没给）")
    else:
        inrange = 0 <= point[0] < cw and 0 <= point[1] < ch
        say(f"[4] 客户区           : {cw} x {ch}")
        say(f"    点击点           : {point}   {'在客户区内 ✔' if inrange else '★ 超出客户区 ✘'}")
        target, tx, ty, path = deepest_child_at(hwnd, *point)
        say(f"    命中窗口         : {hex(target)}  类名 {window_class(target)}")
        say(f"    钻取路径         : {' > '.join(path)}")
        if target != hwnd:
            say(f"    子窗口内坐标     : ({tx}, {ty})  ← 实际会发给它")
            say("    说明             : 命中了子控件，这通常是对的（标准控件由子窗口自己处理鼠标）")

    # ★ 权限探针：逐层探到子控件（只探顶层会假阳性！）
    say("")
    probe = None
    if point is None:
        probe = probe_window(hwnd)
        if probe["ok"]:
            say("[5] 权限探针         : 顶层通过 ✔")
        else:
            say(f"[5] 权限探针         : 顶层被拦 ✘  错误码 {probe['error']}")
            say(f"    原因             : {describe_error(probe['error'])}")
    else:
        hier = probe_hierarchy(hwnd, *point)
        say("[5] 权限探针         : 逐层检测（探针用 WM_MOUSEMOVE，与真实点击同源）")
        for i, c in enumerate(hier["chain"]):
            indent = "    " + "  " * i + ("└ " if i else "")
            flag = "通过 ✔" if c["ok"] else f"被拦 ✘ (错误码 {c['error']})"
            say(f"{indent}{c['hwnd_hex']:<12}{c['class'][:26]:<28}{flag}")
        if hier["blocked"]:
            b = hier["blocked"][0]
            say("")
            say(f"    ★ 拦截点在 {b['hwnd_hex']}（类名 {b['class']}）")
            say(f"      原因：{describe_error(b['error'])}")
            if hier["best"] != hwnd:
                say(f"      可退守到 {hex(hier['best'])}（{window_class(hier['best'])}），"
                    "点击会自动发给它")
            else:
                say("      连顶层都进不去，只能提权或改用 hardware")
            probe = {"ok": False, "error": b["error"]}
        else:
            probe = {"ok": True, "error": 0}

    # 真投递测试（默认不做；诊断默认只读，不误触目标程序）
    say("")
    if not args.doctor_click:
        say("[6] 实投测试         : 已跳过（默认只读探针；要真点一次加 --doctor-click）")
    elif point is None:
        say("[6] 实投测试         : 已跳过（没给坐标）")
    else:
        real = (resolving_click(hwnd, *point, args) if args.deep
                else do_click(hwnd, *point, args))
        if real["ok"]:
            via = {"post": "PostMessageW", "send": "SendMessageTimeoutW",
                   "hardware": "SetCursorPos+mouse_event"}[real["method"]]
            say(f"[6] 实投测试         : {via} 返回成功 ✔  实际发给 {hex(real['hwnd'])}")
            if real.get("fallback_from"):
                say(f"    退守             : 原本命中 {real['fallback_from']} 被拦，已退到父窗口")
            say("    注意             : 这只代表操作系统收下了消息，")
            say("                       不代表目标程序一定会响应")
        else:
            say(f"[6] 实投测试         : 失败 ✘  错误码 {real['error']}")
            say(f"    原因             : {describe_error(real['error'])}")

    # 判定
    say("")
    say("[7] 判定与建议")
    blocked = not probe["ok"]
    if verdict_permission:
        say(f"    ★ {verdict_permission}")
    if blocked:
        if self_il is not None and self_il < 0x2000:
            say("    ★ 根因：本进程完整性级别低于 Medium（受限/沙箱环境）")
            say("      UIPI 会拦下它发给**任何**普通程序的消息 —— 跟目标是谁无关。")
            say("      解法：换普通 PowerShell / cmd / 双击运行，别在沙箱终端里跑。")
            say("      注意：这种环境下 --elevate 多半也会被挡，优先换终端。")
        elif not admin:
            say("    ★ 消息被 UIPI 拦下了。按这个顺序解决：")
            say("      1) 加 --elevate 提权重跑（会弹 UAC）——最直接")
            say("      2) 或加 --elevate 立刻提权")
            say("      3) 若提权后仍被拦，说明目标在更高保护级别")
            say("         （受保护进程 / AppContainer / 跨会话窗口），")
            say("         PostMessage 这条路走不通，只能改用 --method hardware 真点击")
        else:
            say("    ★ 本进程已是管理员仍被拦 —— 目标保护级别更高")
            say("      （受保护进程 / AppContainer 沙箱 / 不同会话）")
            say("      建议：--method hardware 真点击（会动光标，但基本万能）")
        say("    另查：--scan 可以一次看出本机哪些窗口可点、哪些被挡")
    else:
        say("    权限层面没问题，消息能被目标接收入队 ✔")
        say("    若程序仍无反应，按这个顺序试：")
        say("      1) --method send       （某些程序忽略 Post，只吃同步消息）")
        say("      2) --activate          （需要窗口激活才响应）")
        say("      3) --method hardware   （真点击，基本万能，代价是动光标）")
        say("      4) 换坐标：--center 或调整 --pos")
        say("      5) 目标若是独占全屏游戏 / Electron，合成消息可能天然无效")
    say("=" * 68)

    if args.json:
        print(json.dumps({"lines": lines, "target": t, "elevated_self": admin,
                          "elevated_target": elev, "uipi_probe_ok": probe["ok"],
                          "uipi_error": probe["error"]},
                         ensure_ascii=False, indent=2))
    else:
        print("\n".join(lines))
    return 4 if blocked else 0


def cmd_scan(args) -> int:
    """
    扫描所有可见窗口，用 WM_MOUSEMOVE 探针判定每个窗口能不能接收本进程的消息。
    只读，无副作用。用来快速找出 --elevate 是否值得。
    """
    rows = []
    for hwnd in iter_top_level_windows():
        if not user32.IsWindowVisible(hwnd):
            continue
        title = window_title(hwnd)
        if not title:
            continue
        pr = probe_window(hwnd)
        pid = window_pid(hwnd)
        rows.append({
            "hwnd": hwnd, "hwnd_hex": hex(hwnd), "title": title,
            "process": process_name(pid), "pid": pid,
            "elevated": process_is_elevated(pid),
            "integrity": process_integrity(pid),
            "postable": pr["ok"], "error": pr["error"],
            "client_size": describe(hwnd, with_process=False)["client_size"],
        })

    ok_n = sum(1 for r in rows if r["postable"])
    blocked = [r for r in rows if not r["postable"]]
    self_il = self_integrity()

    if args.json:
        print(json.dumps({"self_elevated": is_admin(), "self_integrity": self_il,
                          "total": len(rows), "postable": ok_n, "blocked": blocked},
                         ensure_ascii=False, indent=2))
        return 0

    me = "管理员" if is_admin() else "普通用户"
    print(f"本进程权限：{me}，完整性级别：{integrity_name(self_il)}")
    if self_il is not None and self_il < 0x2000:
        print("★ 警告：IL 低于 Medium（受限沙箱）。UIPI 会拦下发给所有普通程序的消息，")
        print("  这跟目标是谁无关 —— 请换普通 PowerShell / cmd 跑，别在沙箱终端里跑。")
    print(f"可见窗口 {len(rows)} 个：{ok_n} 个可投递，{len(blocked)} 个被 UIPI 拦截\n")
    print(f"{'状态':<8}{'句柄(hex)':<13}{'目标IL':<26}{'进程':<24}{'客户区':<12}标题")
    print("-" * 116)
    for r in sorted(rows, key=lambda r: (r["postable"], r["process"])):
        flag = "可投递" if r["postable"] else "被拦截"
        cw, ch = r["client_size"]
        print(f"{flag:<8}{r['hwnd_hex']:<13}{integrity_name(r['integrity'])[:24]:<26}"
              f"{r['process'][:22]:<24}{str(cw) + 'x' + str(ch):<12}{r['title'][:36]}")

    if blocked:
        print(f"\n被拦截的 {len(blocked)} 个窗口，错误码分布：")
        dist: dict[int, int] = {}
        for r in blocked:
            dist[r["error"]] = dist.get(r["error"], 0) + 1
        for err, n in sorted(dist.items()):
            print(f"  错误码 {err} x{n}  —— {describe_error(err)}")
        if not is_admin():
            print("\n★ 想点这些窗口，可以让程序自己提权：")
            print("    python bgclick.py --elevate --title \"xxx\" --pos X,Y")
            print("  或者直接跑点击命令，它撞到权限墙时会自己申请管理员权限。")
        else:
            print("\n★ 本进程已是管理员，仍被拦说明目标保护级别更高，"
                  "PostMessage 走不通，改用 --method hardware。")
    return 0


def cmd_click(args) -> int:
    if args.count < 0:
        raise AppError("--count 不能为负数")
    if not (args.pos or args.center or args.screen_pos):
        raise AppError("必须指定 --pos / --center / --screen-pos 之一（-h 看帮助）")

    cands = match_windows(args)
    if not cands:
        raise AppError("没找到匹配的窗口。跑一下 --list 看看？", code=1)
    if args.index >= len(cands) or args.index < 0:
        msg = [f"匹配到 {len(cands)} 个窗口，--index {args.index} 越界"]
        msg += [f"  [{i}] {c['hwnd_hex']} {c['process']} | {c['title']}"
                for i, c in enumerate(cands)]
        raise AppError("\n".join(msg))

    target_info = cands[args.index]
    hwnd = target_info["hwnd"]
    cw, ch = target_info["client_size"]

    if len(cands) > 1:
        print(f"注意：匹配到 {len(cands)} 个窗口，这次用的是 [{args.index}]。"
              f"想换一个就加 --index N：", file=sys.stderr)
        for i, c in enumerate(cands[:8]):
            mark = "*" if i == args.index else " "
            print(f"  {mark}[{i}] {c['hwnd_hex']:<12}{c['process'][:20]:<22}"
                  f"{c['client_size'][0]}x{c['client_size'][1]:<8}{c['title'][:40]}",
                  file=sys.stderr)

    if args.center:
        base = (cw // 2, ch // 2)
    elif args.pos:
        base = args.pos
    else:
        base = resolve_client_point(hwnd, *args.screen_pos)

    if not (0 <= base[0] < cw and 0 <= base[1] < ch):
        print(f"警告：基准坐标 {base} 落在客户区 {cw}x{ch} 之外，程序多半收不到这个点击",
              file=sys.stderr)

    meta = {
        "hwnd": hwnd,
        "hwnd_hex": target_info["hwnd_hex"],
        "title": target_info["title"],
        "process": target_info["process"],
        "client_size": [cw, ch],
        "base_pos": list(base),
        "button": args.button,
        "method": args.method,
        "count": args.count,
        "interval": args.interval,
        "elevated": is_admin(),
        "dry_run": args.dry_run,
    }
    if not args.json:
        print(f"本进程权限：{'管理员' if is_admin() else '普通用户'}"
              + ("" if is_admin() else "（目标若是管理员权限，需要 --elevate）"))
        print(f"目标：{target_info['hwnd_hex']}  {target_info['process']}  "
              f"「{target_info['title']}」")
        print(f"客户区 {cw}x{ch}，基准坐标 {base}，{args.button} 键，方式 {args.method}，"
              f"{'无限循环' if args.count == 0 else str(args.count) + ' 次'}，"
              f"间隔 {args.interval}s")
        if args.dry_run:
            print("（dry-run：不发消息）")

    if args.activate and not args.dry_run and args.method != "hardware":
        if not user32.SetForegroundWindow(hwnd):
            print("提示：SetForegroundWindow 被系统拒绝（前台锁），继续尝试投递。",
                  file=sys.stderr)
        time.sleep(0.15)

    done = 0
    failed = 0
    while args.count == 0 or done < args.count:
        if not user32.IsWindow(hwnd):
            print(f"\n目标窗口已关闭，停止。（已点击 {done} 次）", file=sys.stderr)
            return 3

        x, y = base
        if args.jitter:
            x += random.randint(-args.jitter, args.jitter)
            y += random.randint(-args.jitter, args.jitter)
        x, y = max(0, min(x, cw - 1)), max(0, min(y, ch - 1))

        # 先算出实际会命中的窗口（dry-run 只算不发）
        # ★ 真输入方式（hardware / sendinput）不适用「钻子控件」：它们是真的
        #   把光标移过去点，系统自己决定命中谁，探测结果没有意义。
        real_click = args.method in ("hardware", "sendinput")
        hier = (probe_hierarchy(hwnd, x, y)
                if args.deep and not real_click else None)

        if args.dry_run:
            hit = hier["best"] if hier else hwnd
            will_fallback = bool(hier and hier["blocked"] and hier["best"] != hwnd)
            rec = {"method": "dry-run", "hwnd": hit,
                   "point": list(hier["best_point"]) if hier else [x, y],
                   "ok": True, "error": 0, "will_fallback": will_fallback}
            if hier:
                rec["hierarchy"] = [c["hwnd_hex"] for c in hier["chain"]]
                if hier["blocked"]:
                    rec["blocked_at"] = hier["blocked"][0]["hwnd_hex"]
        else:
            # 智能投递：逐层探测，选最深且可投递的窗口（真输入方式不探测，见上）
            if args.deep and not real_click:
                rec = resolving_click(hwnd, x, y, args)
            else:
                rec = do_click(hwnd, x, y, args)
                rec.setdefault("hierarchy", [hex(hwnd)])

        rec.update({"index": done, "client_point": [x, y], **meta})

        if args.dry_run:
            if args.json:
                print(json.dumps(rec, ensure_ascii=False))
            else:
                chain = rec.get("hierarchy")
                extra = f"  层级 {' > '.join(chain)}" if chain else ""
                if rec.get("blocked_at"):
                    if rec.get("will_fallback"):
                        warn = f"  ★ {rec['blocked_at']} 被拦，会退守到 {hex(rec['hwnd'])}"
                    else:
                        warn = f"  ★ {rec['blocked_at']} 被拦且无可退守层（需提权）"
                else:
                    warn = ""
                print(f"[{done + 1}] 将点击客户区 ({x}, {y})，实际发给 {hex(rec['hwnd'])}"
                      f"{extra}{warn}")
        elif rec["ok"]:
            if args.json:
                print(json.dumps(rec, ensure_ascii=False))
            else:
                hit = rec["hwnd"]
                extra = f" -> 子窗口 {hex(hit)}" if hit != hwnd else ""
                if rec.get("fallback_from"):
                    extra += f"（{rec['fallback_from']} 被拦，已退守）"
                print(f"[{done + 1}] 已投递 ({x}, {y}){extra}"
                      + ("  [真点击]" if args.method == "hardware" else ""), flush=True)
        else:
            failed += 1
            err = rec["error"]
            print(f"[{done + 1}] 投递失败！{describe_error(err)}", file=sys.stderr)
            if args.json:
                print(json.dumps(rec, ensure_ascii=False))
            if err == ERROR_ACCESS_DENIED:
                where = rec.get("blocked_at")
                if where:
                    print(f"\n>>> 拦截点：{where['hwnd_hex']}（类名 {where['class']}）",
                          file=sys.stderr)
                # ★ 第一条消息就撞权限墙 → 直接自动提权重跑，别让用户手动加参数
                if done == 0 and not is_admin():
                    code = maybe_auto_elevate(
                        args, hwnd, f"给 {where['hwnd_hex'] if where else hex(hwnd)} 发消息被拒（UIPI）"
                    )
                    if code is not None:
                        return code
                if not is_admin():
                    print(">>> 这是 UIPI：本进程权限不够，消息被系统丢掉了。", file=sys.stderr)
                    print(">>> 加 --elevate 立刻提权重跑（会弹 UAC）",
                          file=sys.stderr)
                    print(">>> 先跑 --doctor 可以看清是顶层还是子控件被拦。", file=sys.stderr)
                    return 4
                print(">>> 已是管理员仍被拦，目标保护级别更高"
                      "（受保护进程 / AppContainer / 跨会话）。", file=sys.stderr)
                print(">>> PostMessage 这条路走不通，改用 --method hardware 真点击。",
                      file=sys.stderr)
                return 4
            if not args.force_continue:
                print(">>> 已停止。想无视失败继续循环就加 --force-continue",
                      file=sys.stderr)
                return 5

        done += 1
        if args.count == 0 or done < args.count:
            time.sleep(max(0.0, args.interval))

    if not args.json:
        tail = f"完成，共 {done} 次。" if failed == 0 else f"完成，共 {done} 次，其中 {failed} 次失败。"
        print(tail)
    return 0 if failed == 0 else 5


def cmd_swipe(args) -> int:
    """
    CLI 入口：滑动 / 拖拽 / 滚轮。

    post / send 走消息投递，不动真实光标；
    hardware 走真输入（SetCursorPos + mouse_event），会占用真实光标但所有程序都吃；
    sendinput 走系统输入队列，整批原子提交（拖拽最稳），也会占用真实光标。
    """
    real_input = args.method in ("hardware", "sendinput")

    cands = match_windows(args)
    if not cands:
        raise AppError("没找到匹配的窗口。跑一下 --list 看看？", code=1)
    if args.index >= len(cands) or args.index < 0:
        msg = [f"匹配到 {len(cands)} 个窗口，--index {args.index} 越界"]
        msg += [f"  [{i}] {c['hwnd_hex']} {c['process']} | {c['title']}"
                for i, c in enumerate(cands)]
        raise AppError("\n".join(msg))

    target = cands[args.index]
    body: dict = {"method": args.method, "delay": args.delay, "hold": args.hold,
                  "ease": args.ease, "no_deep": not args.deep,
                  "release": args.release, "steps": args.steps,
                  "restore_cursor": bool(getattr(args, "restore_cursor", True))}
    if args.from_pos:
        body["from"] = list(args.from_pos)
    if args.duration is not None:
        body["duration"] = args.duration
    if args.scroll is not None:
        body["scroll"] = args.scroll
        body["axis"] = args.axis
    else:
        if args.pattern:
            body["pattern"] = args.pattern
            body["distance"] = args.distance
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
                    raise AppError(f"--path 格式应为 \"x,y;x,y\"，收到 {chunk!r}", code=2)
            body["path"] = pts
        elif args.to_pos:
            body["to"] = list(args.to_pos)
        else:
            body["dx"], body["dy"] = args.dx, args.dy
        if args.drag:
            body["button"] = args.button
        elif args.button != "left":
            body["button"] = args.button

    if not args.json:
        kind = ("滚轮" if args.scroll is not None
                else ("拖拽" if body.get("button") else "悬停滑动"))
        print(f"本进程权限：{'管理员' if is_admin() else '普通用户'}")
        print(f"目标：{target['hwnd_hex']}  {target['process']}  「{target['title'][:40]}」")
        print(f"{kind} / 方式 {args.method} / 步数 {args.steps}"
              + ("（平滑加减速）" if args.ease else ""))
        if real_input:
            print(f"★ {args.method} 真输入：会占用你的真实光标，期间别动鼠标。"
                  "若目标被遮挡，操作的是上层窗口。")
            if not args.restore_cursor:
                print("★ --no-restore-cursor：结束时不会把光标放回去。")

    fake = argparse.Namespace(hwnd_int=target["hwnd"])
    rec = do_mouse(fake, body)
    rec.update({"hwnd": target["hwnd_hex"], "title": target["title"],
                "process": target["process"]})

    if args.json:
        print(json.dumps(rec, ensure_ascii=False))
    else:
        if rec.get("ok"):
            if rec.get("mode") == "scroll":
                print(f"已滚 {rec['amount']} 格（{rec['axis']}），"
                      f"投递 {rec['sent']} 条消息，落在客户区 {rec.get('from')}")
            elif str(rec.get("mode", "")).startswith(("hardware", "sendinput")):
                extra = "，光标已放回原处" if rec.get("cursor_restored") else ""
                if rec.get("batched") is not None:
                    extra = ("，整批原子提交" if rec.get("batched") else "，逐点提交") + extra
                print(f"真输入{'拖拽' if 'drag' in rec.get('mode', '') else '滑动'}完成："
                      f"{rec.get('from')} → {rec.get('to')}，"
                      f"{rec.get('sent')}/{rec.get('expected')} 条事件{extra}")
            else:
                print(f"{'拖拽' if rec.get('button') else '滑动'}完成："
                      f"{rec.get('from')} → {rec.get('to')}，"
                      f"共 {rec.get('steps')} 步 / 投递 {rec.get('sent')} 条消息")
        else:
            err = rec.get("error", 0)
            print(f"投递失败：{describe_error(err)}", file=sys.stderr)
            if err == ERROR_ACCESS_DENIED:
                print(">>> UIPI 拦截：目标完整性级别比本进程高。加 --elevate 重跑。",
                      file=sys.stderr)
            return 4 if err == ERROR_ACCESS_DENIED else 5
    return 0 if rec.get("ok") else 5


def maybe_auto_elevate(args, hwnd: int, reason: str) -> Optional[int]:
    """
    需要时自动提权重跑。返回退出码（表示"已经处理完了，别继续"）或 None（继续跑）。

    只有在「提权确实能解决问题」时才动手 —— 免得平白弹 UAC 骚扰用户：
      * 已经是管理员 → 不提
      * --no-elevate → 不提
      * 已经是被提权拉起的子进程（--_elevated）→ 不提，防止无限套娃
      * 目标 IL <= 自己 IL → 提了也没用，不提
    """
    if not getattr(args, "auto_elevate", True):
        return None
    if args._elevated:
        return None                      # 防重入
    if is_admin():
        return None
    if args.json:                        # JSON 模式别提权，否则调用方拿不到输出
        return None

    # 受限沙箱（IL < Medium）：提权基本也无效，别白弹 UAC 骚扰用户
    me = self_integrity()
    if me is not None and me < 0x2000:
        print(f"\n检测到权限不足：{reason}", file=sys.stderr)
        print(f"★ 但本进程完整性级别是 {integrity_name(me)}（受限沙箱），"
              "UIPI 会拦下它发给所有普通程序的消息。", file=sys.stderr)
        print("  提权在这种环境下通常也无效 —— 请换普通 PowerShell / cmd 重跑。",
              file=sys.stderr)
        return None

    helps = elevation_would_help(hwnd)
    if helps is False:
        return None                      # 权限已经够了，问题在别处

    print(f"\n检测到权限不足：{reason}", file=sys.stderr)
    if helps is True:
        print("目标完整性级别高于本进程 —— 提权可以解决。", file=sys.stderr)
    else:
        print("读不到目标完整性级别，但仍值得试一次提权。", file=sys.stderr)
    print("（不想自动提权就加 --no-elevate）", file=sys.stderr)

    return relaunch_as_admin(getattr(args, "_argv", []))


def interactive_guide() -> int:
    """
    没有任何参数、也没指定目标时（典型场景：双击脚本）的引导流程。
    """
    admin = is_admin()
    il = self_integrity()
    print("=" * 66)
    print("bgclick —— Windows 后台点击器")
    print("=" * 66)
    print(f"当前权限：{'管理员' if admin else '普通用户'}，"
          f"完整性级别：{integrity_name(il)}")
    if il is not None and il < 0x2000:
        print("★ 警告：完整性级别低于 Medium（受限/沙箱环境）")
        print("  UIPI 会拦下发给所有普通程序的消息 —— 请换普通 PowerShell / cmd 运行。")
    print()
    print("用法：")
    print('  python bgclick.py --list                                  看窗口列表')
    print('  python bgclick.py --scan                                  看哪些能点')
    print('  python bgclick.py --doctor --title "xxx" --pos 100,100    诊断点不动的原因')
    print('  python bgclick.py --title "xxx" --pos 400,300             点 1 次')
    print('  python bgclick.py --title "xxx" --center --count 0        无限循环点中心')
    print()
    print("不需要手动提权：撞到权限墙时程序会自己弹 UAC。")
    print("参数详解：python bgclick.py -h")
    print("=" * 66)

    # 有控制台（双击场景）就顺手列出窗口，省得再问一次
    try:
        stdin_tty = sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        stdin_tty = False
    if stdin_tty:
        print("\n当前可见窗口：")
        rows = []
        for hwnd in iter_top_level_windows():
            if not user32.IsWindowVisible(hwnd):
                continue
            title = window_title(hwnd)
            if title:
                rows.append(describe(hwnd))
        rows.sort(key=lambda r: r["process"])
        for i, r in enumerate(rows[:15]):
            cw, ch = r["client_size"]
            ok = probe_window(r["hwnd"])["ok"]
            print(f"  {'✔' if ok else '✘'} {r['hwnd_hex']:<12}{r['process'][:20]:<22}"
                  f"{cw}x{ch:<8}{r['title'][:36]}")
        if len(rows) > 15:
            print(f"  …… 还有 {len(rows) - 15} 个，用 --list 看全部")
        print("\n✔ = 能投递   ✘ = 被 UIPI 拦截")
    return 0


def cmd_shot(args) -> int:
    """截图子命令：抓指定窗口存成图片。"""
    cands = match_windows(args)
    if not cands:
        raise AppError("没找到匹配的窗口。跑一下 --list 看看？", code=1)
    if args.index >= len(cands) or args.index < 0:
        msg = [f"匹配到 {len(cands)} 个窗口，--index {args.index} 越界"]
        msg += [f"  [{i}] {c['hwnd_hex']} {c['process']} | {c['title']}"
                for i, c in enumerate(cands)]
        raise AppError("\n".join(msg))

    target = cands[args.index]
    hwnd = target["hwnd"]

    if len(cands) > 1:
        print(f"注意：匹配到 {len(cands)} 个窗口，用的是 [{args.index}]："
              f"{target['hwnd_hex']} {target['title']}", file=sys.stderr)

    path = args.shot
    fmt = "bmp" if path.lower().endswith(".bmp") else "png"
    if not os.path.splitext(path)[1]:
        path += "." + fmt

    if user32.IsIconic(hwnd):
        print("提示：窗口处于最小化状态。PrintWindow 可能会拿到黑图，"
              "建议先还原窗口。", file=sys.stderr)

    info = save_screenshot(hwnd, path, client_only=args.client_only,
                           fmt=fmt, method=args.shot_method)
    info.update({"hwnd": target["hwnd_hex"], "title": target["title"],
                 "process": target["process"]})

    if args.json:
        print(json.dumps(info, ensure_ascii=False))
    else:
        print(f"已截图：{info['path']}")
        print(f"  窗口 {target['hwnd_hex']}  {target['process']}  「{target['title']}」")
        print(f"  尺寸 {info['width']}x{info['height']}  {info['format'].upper()}  "
              f"{info['bytes']} 字节  方式 {info['capture_method']}"
              + ("  仅客户区" if info["client_only"] else ""))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    args._argv = argv                     # 留给自动提权用
    enable_dpi_awareness()

    # 显式 --elevate：立刻提权重跑
    if args.elevate and not args._elevated and not is_admin():
        return relaunch_as_admin(argv)
    if args.elevate and is_admin() and not args._elevated and not args.json:
        print("已经是管理员权限，直接开跑。")
    elif args._elevated:
        print(f"[已提权运行] 管理员权限 + 完整性级别 {integrity_name(self_integrity())}\n")

    try:
        if args.list or args.list_all:
            code = cmd_list(args)
        elif args.scan:
            code = cmd_scan(args)
        elif args.doctor:
            code = cmd_doctor(args)
        elif args.shot:
            code = cmd_shot(args)
        elif args.swipe or args.scroll is not None:
            code = cmd_swipe(args)
        elif (not argv and args.hwnd is None and not args.title and not args.process
              and not (args.pos or args.center or args.screen_pos)):
            code = interactive_guide()      # 双击/无参数 → 给引导
        else:
            code = cmd_click(args)
    except AppError as e:
        print(str(e), file=sys.stderr)
        code = e.code
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，已停止。", file=sys.stderr)
        code = 0
    except Exception as e:  # 兜底：别把栈糊到用户脸上
        print(f"出错了：{type(e).__name__}: {e}", file=sys.stderr)
        code = 2

    if args.keep_open:
        try:
            input("\n按回车关闭……")
        except EOFError:
            pass
    return code


if __name__ == "__main__":
    sys.exit(main())
