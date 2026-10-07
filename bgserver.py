#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bgserver.py —— 后台点击/截图的常驻本地服务（供 agent skill 调用）

把 bgclick 的能力包成一个**只监听本机**的 HTTP 小服务，常驻后台跑。
首个请求或启动时自动申请管理员权限（弹一次 UAC），之后就一直以管理员身份待命，
不用每次点击都弹窗。

快速开始
--------
  # 启动（会弹 UAC，之后常驻；默认带托盘图标）
  python bgserver.py

  # 查状态
  python bgclient.py health

  # 列出窗口
  python bgclient.py windows

  # 点一下
  python bgclient.py click --title "记事本" --pos 400,300

  # 截图
  python bgclient.py shot --title "记事本" --out shots/note.png

  # 键盘：组合键 / 多键同按 / 逐字符真按键（走 WM_KEYDOWN，不是 WM_CHAR）
  python bgclient.py key --title "记事本" --keys ctrl+shift+s
  python bgclient.py key --title "记事本" --hold ctrl+shift+a --hold-seconds 1.5
  python bgclient.py key --title "记事本" --type "Hello!"

  # 停止
  python bgclient.py shutdown

─────────────── 安全设计（请先读这段）───────────────
这个服务跑在管理员权限下、还能模拟鼠标键盘。所以它是一块**提权面**，
下面每条限制都是刻意的，不是多余：

1. **只绑 127.0.0.1**。绝不监听 0.0.0.0。外网/局域网连不上。
2. **强制 Bearer token**。启动时随机生成，存到 .bgstate/token.txt。
   没有 token 一律 401 —— 防止本机其他用户/其他程序顺手驱动它。
3. **校验 Host 头**，只接受 localhost / 127.0.0.1 / [::1]。
   防 DNS rebinding：否则恶意网页能把你的浏览器变成攻击跳板。
4. **截图落盘路径被限制**在允许的根目录内（默认 ./shots 和 ./.bgstate），
   防止被用来覆盖任意文件。
5. **点击次数有上限**（默认单次请求最多 10000 次），
   防止一个错误请求把目标程序点爆。
6. **不提供任意命令执行**。只有点击/截图/文本这几个动作，没有 shell。
7. token 文件权限设为仅当前用户可读。

如果这些限制挡了你的正当用法，用命令行参数放开（--max-clicks 等），
不要去改代码里的检查。

本文件相对最初版本的修复（1.0.1）：
  1. 顶部 import subprocess（do_restart 用到）。
  2. state_dir() 与 bgclient 完全对齐。
  3. 托盘默认开启（不再依赖 sys.frozen）。
  4. do_restart 给新进程带 --_wait-for-port-release，
     避免新进程在老进程端口还没释放时误判「已有实例」而直接退出。
  5. 托盘初始化失败不再静默退回无托盘模式：写日志 + 弹 MessageBox。

1.1.0 新增：
  6. /key 接口 —— 键盘输入。单键、组合键（ctrl+shift+s）、多键同按（按住不放）、
     逐字符真按键。走 WM_KEYDOWN/WM_KEYUP + 规范 lParam，不是原来只有的 WM_CHAR。
     原来的 /text 保留不动（纯字符通道，适合中英文文本）。
  7. /mouse 接口 —— 鼠标滑动 / 拖拽 / 滚轮。相对位移、绝对坐标、途经点、手势，
     以及按住拖动（每步带按键状态位，否则程序会认为中途松手）。
     坐标是客户区坐标；滚轮的 lParam 用屏幕坐标（Windows 规定）。

1.2.1 修复：
  8. /key 的 text 模式不再对同一个字符同时发 WM_KEYDOWN 和 WM_CHAR。
     两条通道都被控件吃下时字符会翻倍（记事本里 "Hello!" → "Hheelllloo!1"），
     而且按键通道翻译出的是小写（真实键盘没按着 Shift）。
     默认改成每个字符只走一条通道，另提供 "mode": "keys" / "both"。

1.3.0 新增：
  9. /mouse 支持 method="hardware" 真输入（SetCursorPos + mouse_event）：
     真移动光标、真按下、真滚轮。给「合成消息当没发生」的程序用 ——
     鼠标位置是全局状态，程序会去查真实光标，消息伪造不了状态，
     所以这类程序的滑动/拖拽只有真输入一条路。代价是占用真实光标（前台行为）。

1.4.0 新增：
 10. method="sendinput"：走系统输入队列（SendInput），鼠标轨迹/拖拽可**整批原子提交**
     （不会被用户真实鼠标插队拆散），坐标走 VIRTUALDESK 归一化。
 11. /key 的 sendinput 模式：默认只发**扫描码**（KEYEVENTF_SCANCODE，wVk=0）。
     只读底层键盘输入的程序（DirectInput / Raw Input 类）对虚拟键码不买账、
     对扫描码买账 —— 这就是「用扫描码」在键盘上的真实含义。
     ★ 注意：扫描码是键盘概念，MOUSEINPUT 里没有扫描码字段；
       鼠标侧的对应物是 SendInput 的 MOUSEEVENTF_MOVE|ABSOLUTE。
     ★ 能力边界：SendInput 注入仍会被 Raw Input 识别（hDevice=NULL，
       低级钩子有 LLMHF_INJECTED）。要「像真实硬件」只能上驱动级虚拟 HID，
       不在本项目范围内。

1.5.0 新增：
 12. /uia 接口 + `uia` 子命令 —— UI Automation 元素查询。用系统自带的 UIA 读窗口的
     元素树，按 name / control_type / automation_id / class_name 找元素，
     直接拿到 rect（屏幕坐标）和 client_center（客户区坐标）。
     解决「坐标要靠截图数像素」这个老问题：先查元素拿坐标，再交给 /click 去点，
     那条路仍然不抢光标。
     * `limit`（默认 500）超了返回**部分结果**并置 truncated=true，不报错 ——
       UIA 碰上几千行的列表会把目标程序卡住，上限必须留着。
     * 默认只读：walk / find 都是纯查询，只有 `--uia-click` 才真的点。
     * 纯 ctypes 实现，没用 comtypes / uiautomation 包。
     * /health 里多一个 `uia: {available, message}`，一眼看出这个 build 有没有带 UIA。
"""

# ========================================================================
# 兼容壳：真正的实现已经拆到 bgkit/bgserver/ 下面了。
# 这个文件保留原来的名字和用法，老的调用一律不用改：
#     import bgserver as bc        →  bc.user32 / bc.match_windows / ...
#     python bgserver.py --list       →  命令行照旧
# ========================================================================

from bgkit.bgserver import *  # noqa: F401,F403

# import * 不会带出下划线开头的名字，补上以保持符号面一致
from bgkit.bgserver import _early_log_candidates, _find_focus_window, _main_inner  # noqa: F401


if __name__ == "__main__":
    import sys

    from bgkit.bgserver.cli import main as _main

    sys.exit(_main())
