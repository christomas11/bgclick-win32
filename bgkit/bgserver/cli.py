# -*- coding: utf-8 -*-
"""bgkit.bgserver.cli.py —— 命令行与主流程

从最初的单文件脚本拆出的一节。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

from . import config
from bgkit import ROOT
from bgkit import bgclick as bc
from bgkit import entry_script
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import os
import subprocess
import sys
import threading
import time
import traceback
from typing import Optional
from .api import Handler
from .bootstrap import fatal_report, relaunch_elevated
from .config import ALLOWED_SHOT_ROOTS, DEFAULT_PORT, VERSION, load_or_create_token, pid_path, port_path, state_dir, token_path
from .logfile import Logger
from .singleton import SingleInstance, find_existing



def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bgserver.py",
        description="后台点击/截图的常驻本地服务（仅监听 127.0.0.1）",
    )
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"端口（默认 {DEFAULT_PORT}）")
    p.add_argument("--token", help="固定 token（默认自动生成并存在 .bgstate/token.txt）")
    p.add_argument("--elevate", action="store_true", help="启动时申请管理员权限（默认自动）")
    p.add_argument("--no-elevate", dest="auto_elevate", action="store_false", default=True,
                   help="不提权，以当前权限运行（很多窗口会点不动）")
    p.add_argument("--_elevated", action="store_true", help=argparse.SUPPRESS)
    # 重启场景：新进程带这个标记时会先等端口释放，避免误判「已有实例」
    p.add_argument("--_wait-for-port-release", action="store_true",
                   help=argparse.SUPPRESS)
    p.add_argument("--shot-dir", default="shots", help="允许截图落盘的目录（默认 ./shots）")
    p.add_argument("--extra-shot-dir", action="append", default=[],
                   help="额外的允许截图目录，可重复")
    p.add_argument("--max-clicks", type=int, default=10000,
                   help="单次请求允许的最大点击次数（默认 10000）")
    p.add_argument("--verbose", action="store_true", help="打印每个请求日志")

    # ★ 托盘默认开启：`--tray` 保留为兼容参数（显式给也不报错），
    #   真正控制开关的是 `--no-tray`。argparse 里同一个 dest 被两个参数操作时，
    #   最后定义的那个参数的 default 生效 —— 所以 --no-tray 显式写 default=True，
    #   --tray 用 default=argparse.SUPPRESS 避免覆盖它。
    p.add_argument("--tray", action="store_true", default=argparse.SUPPRESS,
                   help="显示系统托盘图标（默认就开；给不给都一样）")
    p.add_argument("--no-tray", dest="tray", action="store_false", default=True,
                   help="不显示托盘图标（纯后台跑，适合 CI / 无桌面环境）")

    p.add_argument("--no-console", action="store_true",
                   help="抑制 stdout 输出（无窗口 exe 下只能靠日志文件）")
    p.add_argument("--open-log", action="store_true", help="启动时打开日志窗口")
    return p




def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    bc.enable_dpi_awareness()

    try:
        return _main_inner(args, argv)
    except SystemExit:
        raise
    except BaseException as e:
        # ★ 无窗口 exe 绝不能把异常悄悄吞掉 —— 用户会看到「双击没反应」。
        fatal_report("bgclick 服务启动失败",
                     f"{type(e).__name__}: {e}")
        return 1


def _main_inner(args, argv: list[str]) -> int:
    log = Logger(os.path.join(state_dir(), "bgserver.log"),
                 also_stdout=not args.no_console)

    # ★ 托盘默认开启（不再依赖 sys.frozen）。
    #   只有显式给了 --no-tray 才关 —— 比如 CI、无桌面会话、被系统服务方式拉起时。
    if "--no-tray" in argv:
        log.write("已指定 --no-tray：以纯后台模式运行，不创建托盘图标。")
    else:
        log.write("托盘模式：将创建系统托盘图标（想看图标请展开任务栏的 ^ 折叠区）。")

    # 提权（默认行为）。但提权**可能悄悄失败**（沙箱里 ShellExecuteW 会假报成功），
    # 而且在受限沙箱里提权本来也没用 —— 所以这里的逻辑是：
    # 先试提权；只有当「试了会死」的时候才不试。
    if args.auto_elevate and not args._elevated and not bc.is_admin():
        me = bc.self_integrity()
        if me is not None and me < 0x2000:
            # 受限沙箱：提权救不了（UIPI 看的是 IL），直接不提，省得白等
            log.write(f"提示：完整性级别 {bc.integrity_name(me)}（受限环境），"
                      "提权对 UIPI 无效，直接以当前权限启动。")
        else:
            return relaunch_elevated(argv)

    # ★ 重启场景：新进程可能比老进程的端口释放「抢跑」。
    #   带 --_wait-for-port-release 时，先轮询等端口空出来，再走单实例检查。
    #   这是「重启服务只执行到退出、没有重新启动」的直接修复。
    if args._wait_for_port_release:
        deadline = time.time() + 10.0
        waited = False
        while time.time() < deadline:
            if not find_existing(args.port):
                break
            if not waited:
                log.write(f"等待端口 {args.port} 释放（老实例正在退出）……")
                waited = True
            time.sleep(0.3)
        if waited:
            log.write("端口已释放，继续启动。")

    # 单实例检查：端口已有服务就别再起一个
    pid_existing = find_existing(args.port)
    if pid_existing:
        msg = f"端口 {args.port} 上已有服务在运行（PID {pid_existing}），不重复启动。"
        log.write(msg)
        if args.tray:
            # 让用户知道「其实已经在跑了」，而不是以为双击没反应
            try:
                from bgkit import bgtray
                t = bgtray.TrayApp(title="bgclick", menu_items=[("退出", lambda: None)])
                threading.Thread(target=t.run, daemon=True).start()
                t.wait_ready(3)
                t.notify("bgclick 服务已在运行", msg)
                time.sleep(4)
            except Exception as e:
                log.write(f"提示气泡创建失败：{type(e).__name__}: {e}")
        return 0

    lock = SingleInstance(f"bgclick_server_{args.port}")
    if not lock.acquire():
        log.write(f"已有另一个 bgclick 实例占用端口 {args.port}，退出。")
        return 0

    # 截图根目录：相对路径按**启动时的工作目录**解析，
    # 这样 agent 在哪个工程目录跑，截图就落在那个目录的 shots/ 下，方便直接查看。
    launch_cwd = os.getcwd()

    def _abs_shot(d: str) -> str:
        return os.path.abspath(d if os.path.isabs(d) else os.path.join(launch_cwd, d))

    roots = [_abs_shot(args.shot_dir)]
    roots += [_abs_shot(d) for d in args.extra_shot_dir]
    roots.append(state_dir())
    # ★ 必须是就地更新（见 config.set_allowed_shot_roots 的说明），
    #   重新绑定只会改到本模块的副本，security 那边读到的还是空列表。
    config.set_allowed_shot_roots(roots)
    for r in ALLOWED_SHOT_ROOTS:
        os.makedirs(r, exist_ok=True)

    token = load_or_create_token(args.token)

    # 只绑本机回环地址 —— 这条是硬安全边界，不要改
    bind_host = "127.0.0.1"
    try:
        httpd = ThreadingHTTPServer((bind_host, args.port), Handler)
    except OSError as e:
        log.write(f"启动失败：端口 {args.port} 可能已被占用（{e}）。")
        if args.tray:
            try:
                from bgkit import bgtray
                t = bgtray.TrayApp(title="bgclick")
                threading.Thread(target=t.run, daemon=True).start()
                t.wait_ready(3)
                t.notify("bgclick 启动失败", f"端口 {args.port} 被占用")
                time.sleep(4)
            except Exception as e2:
                log.write(f"提示气泡创建失败：{type(e2).__name__}: {e2}")
        lock.release()
        return 1

    httpd.token = token                     # type: ignore[attr-defined]
    httpd.started = time.time()             # type: ignore[attr-defined]
    httpd.verbose = args.verbose            # type: ignore[attr-defined]
    httpd.max_clicks = max(1, args.max_clicks)   # type: ignore[attr-defined]
    httpd.daemon_threads = True

    with open(port_path(), "w", encoding="utf-8") as f:
        f.write(str(args.port))
    with open(pid_path(), "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    elevated = bc.is_admin()
    log.write("=" * 60)
    log.write(f"bgserver {VERSION} 已启动")
    log.write(f"  监听地址 : http://{bind_host}:{args.port}   （仅本机可访问）")
    log.write(f"  权限     : {'管理员 ✔' if elevated else '普通用户 ✘（很多窗口会点不动）'}")
    log.write(f"  完整性级 : {bc.integrity_name(bc.self_integrity())}")
    log.write(f"  托盘     : {'开' if args.tray else '关（--no-tray）'}")
    log.write(f"  token    : {token_path()}")
    log.write(f"  截图目录 : {', '.join(ALLOWED_SHOT_ROOTS)}")
    log.write(f"  日志     : {log.path}")
    log.write("=" * 60)

    # 服务跑在后台线程；托盘消息循环要占主线程（Win32 托盘消息必须由创建它的线程处理）
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    def shutdown_all():
        try:
            httpd.shutdown()
        except Exception:
            pass
        try:
            httpd.server_close()
        except Exception:
            pass
        for p in (port_path(), pid_path()):
            try:
                os.remove(p)
            except OSError:
                pass
        lock.release()
        log.write("服务已停止。")

    # 无托盘模式：主线程只是等服务器退出（Ctrl+C 或 /shutdown 接口）
    if not args.tray:
        try:
            while server_thread.is_alive():
                server_thread.join(0.5)
        except KeyboardInterrupt:
            log.write("收到 Ctrl+C，正在停止……")
        shutdown_all()
        return 0

    # ---------- 托盘模式 ----------
    # ★ 托盘创建失败**不再静默退回无托盘**：写清楚日志、弹 MessageBox、
    #   并继续以无托盘模式跑服务（服务本身不能因为托盘的 UI 问题而停）。
    tray = None
    try:
        from bgkit import bgtray
        from bgkit.bgtray.shell import _open_path
    except Exception as e:
        log.write(f"★ 无法导入 bgtray：{type(e).__name__}: {e}")
        log.write("  服务继续以无托盘模式运行。检查 bgtray.py 是否在脚本同目录。")
        try:
            bc.user32.MessageBoxW(
                None,
                f"托盘模块 bgtray.py 加载失败，服务将以无托盘模式继续运行。\n\n"
                f"{type(e).__name__}: {e}\n\n日志：{log.path}",
                "bgclick 托盘启动失败", 0x00000030)  # MB_ICONWARNING
        except Exception:
            pass
        try:
            while server_thread.is_alive():
                server_thread.join(0.5)
        except KeyboardInterrupt:
            log.write("收到 Ctrl+C，正在停止……")
        shutdown_all()
        return 0

    def do_shutdown():
        """从托盘菜单退出：先关服务，再让托盘消息循环结束。"""
        log.write("从托盘菜单请求退出……")
        shutdown_all()
        if tray is not None:
            tray.quit()

    def do_restart():
        """
        从托盘菜单重启。

        ★ 修复「只退出没重启」：
          之前的实现是 Popen(新进程) → do_shutdown()。
          问题在于 httpd.shutdown() 是**异步**的 —— 端口不会立即释放，
          新进程启动时跑 find_existing(port) 还能探到老进程，于是判定
          「已有实例在跑」并直接退出；随后老进程才真正关闭。结果就是
          看到「进程退出了但没重启」。

          现在给新进程加 --_wait-for-port-release 标记：它启动后会轮询
          /health，直到老进程的端口彻底不响应，再继续走单实例检查。
          这样就不会误判。
        """
        log.write("从托盘菜单请求重启……")
        try:
            exe = sys.executable
            base = os.path.basename(exe).lower()
            if base.startswith("pythonw"):
                cand = os.path.join(os.path.dirname(exe), "python.exe")
                if os.path.exists(cand):
                    exe = cand

            # 清掉会干扰新实例的参数：
            #   --_elevated                让新进程自己重新走提权逻辑
            #   --_wait-for-port-release   如果老进程是用它启动的，别带过去
            clean_argv = [a for a in argv
                          if a not in ("--_elevated", "--_wait-for-port-release")]
            clean_argv.append("--_wait-for-port-release")

            if getattr(sys, "frozen", False):
                cmd = [exe] + clean_argv
            else:
                cmd = [exe, entry_script("bgserver.py")] + clean_argv

            log.write(f"拉起新实例：{subprocess.list2cmdline(cmd)}")
            subprocess.Popen(cmd, cwd=launch_cwd, close_fds=True)
        except Exception as e:
            log.write(f"重启失败（拉起新进程时出错）：{type(e).__name__}: {e}")
            try:
                if tray is not None:
                    tray.notify("重启失败", f"{type(e).__name__}: {e}")
            except Exception:
                pass
            return   # 新进程都没起来，别把当前实例关了

        # 新进程已经拉起（它会等端口释放）。现在关掉自己，让出端口。
        do_shutdown()

    def status_text() -> str:
        return (f"{'管理员' if elevated else '普通用户'} / "
                f"{bc.integrity_name(bc.self_integrity())} / "
                f"端口 {args.port}")

    try:
        tray = bgtray.TrayApp(
            title="bgclick 服务",
            tooltip=f"bgclick 服务运行中（{'管理员' if elevated else '普通用户'}，端口 {args.port}）",
            icon_path=os.path.join(ROOT, "bgclick.ico"),
            menu_items=[
                ("查看状态（双击我）", lambda: _open_path(log.path)),
                ("-", lambda: None),
                ("打开日志", lambda: _open_path(log.path)),
                ("打开截图目录", lambda: _open_path(ALLOWED_SHOT_ROOTS[0])),
                ("打开状态目录", lambda: _open_path(state_dir())),
                ("复制 Token 路径", lambda: _open_path(token_path())),
                ("-", lambda: None),
                ("重启服务", do_restart),
                ("退出（停止服务）", do_shutdown),
            ],
            on_quit=shutdown_all,
            on_status=status_text,
        )
    except Exception as e:
        log.write(f"★ 托盘对象创建失败：{type(e).__name__}: {e}")
        log.write(traceback.format_exc()[-1500:])
        try:
            bc.user32.MessageBoxW(
                None,
                f"托盘初始化失败，服务将以无托盘模式继续运行。\n\n"
                f"{type(e).__name__}: {e}\n\n日志：{log.path}",
                "bgclick 托盘启动失败", 0x00000030)
        except Exception:
            pass
        try:
            while server_thread.is_alive():
                server_thread.join(0.5)
        except KeyboardInterrupt:
            log.write("收到 Ctrl+C，正在停止……")
        shutdown_all()
        return 0

    # ★ _open_path 现在会抛异常（不再静默吞），托盘回调的 try 能接住。
    #   为了让菜单项调用的 _open_path 走托盘回调的异常处理，这里再包一层，
    #   保证任何异常都能弹气泡告知用户。
    import functools

    def _menu_open(path):
        def _cb():
            try:
                _open_path(path)
            except Exception as e:
                # 自己再兜一层，保证「点了没反应」变成「弹气泡说为什么」
                try:
                    tray.notify("打开失败", f"{type(e).__name__}: {e}")
                except Exception:
                    pass
                # 同时写日志，日志是最可靠的线索
                log.write(f"打开路径失败：{path} -> {type(e).__name__}: {e}")
        return _cb

    # 重新构造菜单项，把 _open_path 的调用换成带兜底的包装
    tray.menu_items = [
        ("查看状态（双击我）", _menu_open(log.path)),
        ("-", lambda: None),
        ("打开日志", _menu_open(log.path)),
        ("打开截图目录", _menu_open(ALLOWED_SHOT_ROOTS[0])),
        ("打开状态目录", _menu_open(state_dir())),
        ("复制 Token 路径", _menu_open(token_path())),
        ("-", lambda: None),
        ("重启服务", do_restart),
        ("退出（停止服务）", do_shutdown),
    ]

    try:
        tray.run()          # 阻塞直到托盘退出
    except KeyboardInterrupt:
        log.write("收到 Ctrl+C，正在停止……")
        shutdown_all()
    except Exception as e:
        log.write(f"★ 托盘消息循环异常：{type(e).__name__}: {e}")
        log.write(traceback.format_exc()[-1500:])
        try:
            bc.user32.MessageBoxW(
                None,
                f"托盘消息循环崩溃，服务将以无托盘模式继续运行。\n\n"
                f"{type(e).__name__}: {e}\n\n日志：{log.path}",
                "bgclick 托盘异常", 0x00000030)
        except Exception:
            pass
        try:
            while server_thread.is_alive():
                server_thread.join(0.5)
        except KeyboardInterrupt:
            pass
        shutdown_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
