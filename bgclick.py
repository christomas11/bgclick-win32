#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bgclick.py —— Windows 后台窗口点击器（纯消息投递，不抢光标、不抢焦点）

原理：用 PostMessageW 把 WM_LBUTTONDOWN / WM_LBUTTONUP 投递到目标窗口的消息队列。
      光标位置、前台窗口、键盘焦点全都不动 —— 你可以同时干别的事。

用法速查
--------
  # 0) 出问题了？先跑诊断，它会把「为什么点不动」直接照出来
  python bgclick.py --doctor --title "xxx" --pos 400,300

  # 1) 先看有哪些窗口（拿句柄和标题）
  python bgclick.py --list

  # 2) 按窗口标题模糊匹配，点「客户区」左上角偏移 (400, 300) 的位置，点 3 次
  python bgclick.py --title "记事本" --pos 400,300 --count 3 --interval 1.0

  # 3) 目标是以管理员身份运行的 → 自己提权跑
  python bgclick.py --elevate --title "xxx" --pos 400,300
  #    或直接跑点击命令 —— 撞到权限墙时它会自己弹 UAC

  # 4) 用完整标题 + 进程名双重确认（更安全，避免误伤同名窗口）
  python bgclick.py --title "计算器" --exact --process ApplicationFrameHost.exe --center

  # 5) 虚跑一遍：只解析坐标、不发消息
  python bgclick.py --title "xxx" --pos 400,300 --dry-run

  # 6) 无限循环，每次 5 秒，偏移抖动 ±3 像素
  python bgclick.py --title "xxx" --pos 400,300 --count 0 --interval 5 --jitter 3

键盘（1.1.0 新增，也是纯消息投递，同样不抢焦点）
--------------------------------------------------
  原来只有 WM_CHAR 文本通道，只对编辑框有效；菜单快捷键、IDE、游戏、
  画布控件都是直接读 WM_KEYDOWN 的虚拟键码，对 WM_CHAR 一律当没看见。

  标准投递（send_chord / send_key_sequence / send_text_as_keys）：
    send_text_as_keys(hwnd, "Hello!")          逐字符真按键（Shift 自动补上）
    send_chord(hwnd, parse_chord("ctrl+shift+s"))
    send_key_sequence(hwnd, [parse_chord("ctrl+a"), parse_chord("ctrl+c")])

   ★ 多键同按（按住不放）用 send_keys_state —— 只发 down 或只发 up，不配对：
    vks = parse_key_list(["ctrl", "shift", "a"])
    send_keys_state(hwnd, vks, keyup=False)     # 三键同时按住
    time.sleep(1.5)                             # 想按多久按多久
    send_keys_state(hwnd, list(reversed(vks)), keyup=True)   # 倒序松开

   lParam 按 Windows 规范组装（扫描码 / 扩展键 / Alt 的 SYS 消息升级都处理了），
   Alt 组合会自动走 WM_SYSKEYDOWN/UP。详见「键盘」那一节的注释。

鼠标滑动 / 拖拽 / 滚轮（1.2.0 新增）
------------------------------------
   do_mouse(args, body)   统一入口，服务端 /mouse 和 CLI 共用
   mouse_swipe(...)       悬停滑动：只发 WM_MOUSEMOVE，wParam=0
   mouse_drag(...)        ★ 拖拽：每一步的移动消息都带 MK_LBUTTON 等按键状态位。
                          不带的话程序认为中途已经松手，拖动会断在起点 ——
                          这是滑动类需求最常见的坑，别省这一步。
   mouse_scroll(...)      滚轮：wParam 高 16 位是增量，lParam 用**屏幕坐标**
   plan_path(...)         直线插值；取整用 round（用 int 会让短距离滑动全塌到起点）

   ★ 滑动不是「发一条消息」：拖动靠连续 WM_MOUSEMOVE 累积，所以默认按 30 步
     插值走过去。步数少 = 程序判成 0 距离；步数多 = 慢，但更像人手。

权限（重要！）
--------------
Windows 的 UIPI（用户界面特权隔离）规则：**只有完整性级别(IL) >= 目标 IL 的进程，
才能给目标窗口发消息**；低发高一律丢弃，PostMessageW 返回 FALSE + 错误码 5。

关键实测结论（别踩这个坑）：
  * UIPI 只放行少数「无害」消息（WM_NULL、WM_MOVE 等）。
    ★ 所以拿 WM_NULL 当探针永远是「通过」，纯假阳性 —— 本脚本用 WM_MOUSEMOVE 探针。
  * 鼠标消息（WM_MOUSEMOVE / WM_LBUTTONDOWN / WM_USER / WM_APP ...）一律走 UIPI 过滤。
  * 普通程序 = Medium。管理员提权 = High。
    ★ 如果本进程是 Low（受限/沙箱终端就长这样），
      它发不进**任何**普通程序 —— 这跟目标是谁无关，提权也救不了。
      解法是换普通 PowerShell / cmd / 双击运行。

自检与解决：
  ★ 程序自己会申请管理员权限 —— 这是默认行为
    第一次发消息就撞上 UIPI（错误码 5）时，它会自动弹 UAC 并在新的管理员
    窗口里继续跑，你不用手动加任何参数。
      --elevate       立刻提权重跑（不等检测）
      --no-elevate    关掉自动提权
    它不会乱弹 UAC：只有「提权确实能解决」时才弹
    （已提权 / 目标 IL 不比自己高 / 自己处在受限沙箱里 → 都不弹）。

  --doctor        只读诊断：自己 IL 多少、目标 IL 多少、卡在哪一层、该走哪条路
  --scan          一次列出本机哪些窗口可点、哪些被挡
  --method hardware  提权也点不动时的终极手段：真点击（会动光标，但基本万能）

点了没反应的排查顺序（从最常见的开始）
--------------------------------------
  1. 本进程在受限/沙箱环境（IL=Low）→ 换普通终端，这是最容易忽略的
  2. 目标程序是管理员权限（IL=High）→ 用 --elevate
  3. 坐标不对 → 先 --dry-run，或用 --center 排除坐标问题
  4. 点在子控件上 → 本脚本默认逐层探测，自动选最深的可投递窗口
  5. 程序不吃合成消息 → 换 --method send，或退到 --method hardware
  6. 需要窗口激活才响应 → 加 --activate

参数
----
  --title TEXT        窗口标题，默认子串匹配（不区分大小写）
  --regex             标题按正则匹配
  --exact             标题必须完全相等
  --process NAME      同时要求进程名（如 notepad.exe）
  --hwnd N            直接指定窗口句柄（十进制或 0x 开头）
  --index N           匹配多个窗口时选第 N 个（默认 0），配合 --list 用
  --pos X,Y           相对客户区左上角的偏移（X/Y 可写十进制，也接受 0x 前缀）
  --center            点客户区正中心（与 --pos 二选一）
  --screen-pos X,Y    屏幕绝对坐标
  --button            左键/右键/中键，默认 left
  --count N           点击次数，0 = 无限循环（默认 1）
  --interval SEC      间隔秒数（默认 0.5）
  --jitter N          每次点击的随机像素抖动，默认 0
  --method M          post（默认，纯后台）/ send / hardware（真点击，会动光标）
  --no-deep           不向下钻取子窗口，直接发给顶层窗口
  --activate          点击前先把窗口调到前台（默认关）
  --elevate           提权后重新运行（会弹 UAC）
  --doctor            只读诊断模式
  --dry-run           只打印将要点击的位置，不发消息
  --json              以 JSON 输出
  --keep-open         结束时等回车（提权重启后自动开）

退出码：0 成功 / 1 找不到窗口 / 2 参数错误 / 3 目标窗口提前关闭
        4 权限被拒（UIPI，需要 --elevate） / 5 消息投递失败

已知限制（不是 bug，是 Windows 的设计）
----------------------------------------
* 消息投递绕过真实输入队列，所以有些程序会「装死」：
  - 用 DirectX / Vulkan 独占渲染的游戏、部分 Unity 全屏程序
  - 用 Raw Input 或低级钩子自己读鼠标的软件
  - Chrome / Electron 的部分区域对合成消息不敏感
  这些情况可以试 --activate，或 --method hardware。
* 有些程序还会检查「鼠标是否真的在窗口上」，这种只能靠真点击。
* 无边框全屏窗口的客户区坐标 = 屏幕坐标，别把 --pos 当成全屏比例。
* PostMessageW 返回成功只代表**操作系统收下了这条消息**，
  不代表目标程序一定会响应 —— 这两件事必须分开看。

作者：Christina（本助手）  // c. 2026-10
"""

# ========================================================================
# 兼容壳：真正的实现已经拆到 bgkit/bgclick/ 下面了。
# 这个文件保留原来的名字和用法，老的调用一律不用改：
#     import bgclick as bc        →  bc.user32 / bc.match_windows / ...
#     python bgclick.py --list       →  命令行照旧
# ========================================================================

from bgkit.bgclick import *  # noqa: F401,F403

# import * 不会带出下划线开头的名字，补上以保持符号面一致
from bgkit.bgclick import _HGDI_ERROR, _INPUTUNION, _KEY_TABLE, _clamp_int  # noqa: F401
from bgkit.bgclick import _dispatch_key_msg, _elevation_command, _integrity_from_token, _is_alt_vk  # noqa: F401
from bgkit.bgclick import _is_modifier_vk, _key_input_for, _mk_key_input, _mk_mouse_input  # noqa: F401
from bgkit.bgclick import _mouse_move_msg, _path_from_spec, _png_chunk, _python_for_elevation  # noqa: F401
from bgkit.bgclick import _resolve_key_token, _send_inputs, _stalled, _to_absolute  # noqa: F401


if __name__ == "__main__":
    import sys

    from bgkit.bgclick.cli import main as _main

    sys.exit(_main())
