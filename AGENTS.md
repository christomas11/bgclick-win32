
# bgclick — 给 AI agent 的使用说明

后台操作 Windows 窗口：**点击 / 键盘 / 滑动 / 截图**。
默认走消息投递，不抢用户的光标和焦点 —— 用户可以在你干活的同时继续用电脑。

这份文档是给**自动化 agent** 看的操作手册：怎么写命令、哪几条路不能自己走、
卡住了按什么顺序排查、哪些坑已经踩过。人类用户看 [`README.md`](README.md)。

> **路径变量**（下面的命令里按你机器的实际路径替换）：
>
> | 变量 | 含义 |
> |---|---|
> | `$REPO` | 本仓库的根目录 |
> | `$BG` | `$REPO\bgclient.py` —— 日常入口，零依赖，直接 subprocess 调它 |
> | `$CLICK` | `$REPO\bgclick.py` —— 诊断 / UIA 查询用，不依赖服务 |
> | `$EXE` | 打包好的 `bgserver.exe` 完整路径 |
> | `$WS` | 当前会话的工作目录，截图默认落它下面 |

用 `PostMessageW` 把鼠标和键盘消息**投递**进目标窗口的消息队列，光标不动、焦点不变，
用户可以同时用电脑。截图用 `PrintWindow` 让窗口自己画自己，被遮挡也能抓。

能做的事：**点击**（左/右/中键）、**键盘**（组合键、多键同按、逐字符）、
**鼠标滑动/拖拽/滚轮**、**截图**。默认全部走消息投递（不抢光标和焦点）。

## ⛔ 硬规则：滑动的真输入默认不许用

**鼠标滑动的 `--method hardware` / `--method sendinput` 会抢占用户的真实光标。**
除非用户**明确要求**（说了「用真滑动 / 抢鼠标也行 / 后台那条路走不通就上真输入」之类），
否则：

* 不要自己把 `mouse` 的 method 升到 `hardware` 或 `sendinput`；
* 不要在报告里「顺手」执行一条真输入命令做对比；
* 滑动没反应时，**先如实报告 + 说明还有真输入这条路，让用户决定**，
  不要自作主张切过去。

原因很实际：真输入会把用户的鼠标从正在干的活里拽走，期间他没法操作电脑。
点击 / 键盘的真输入代价小得多（点一下、敲几个键），通常可以直接用 ——
但**跑之前要看一眼用户是不是正在用鼠标键盘**，别把人家的操作打断。

需要确认时，一句话问用户就行：
> 合成消息这条路对这个程序不生效。还有一条真输入的路（会占用你几秒鼠标，期间别动），
> 要我试吗？

## 文件位置

```
bgclient.py             ★ 日常入口，零依赖（纯标准库），直接 subprocess 调它
bgclick.py              核心库 + 独立 CLI（诊断 / UIA 查询用，不依赖服务）
bgserver.py             常驻服务的源码 / 打包入口
bgtray.py               托盘图标模块
bgkit\                  ★ 真正的实现（36 个模块），上面四个 .py 只是兼容壳
bgserver.spec           PyInstaller 打包配置
```

> ★ **`bgkit\` 别漏**：`bgclick.py` / `bgclient.py` / `bgserver.py` / `bgtray.py`
> 都只是十来 KB 的**兼容壳**，真身在 `bgkit\` 里。只拷那几个 `.py` 会直接
> `ModuleNotFoundError`。

> **版本**：服务 v1.5.0（`/key` 键盘、`/mouse` 滑动、真输入方式、**UIA 元素查询**）。
> `python $BG health` 里除了 `version`，还会给 `uia: {available, message}` ——
> 一眼看出这个 build 有没有带 UIA。旧 build 没这个字段，`uia` 子命令也会 404。

调用方式（**用绝对路径，别依赖 cwd**）：

```powershell
$BG    = "$PWD\bgclient.py"     # 换成你机器上的实际路径
$EXE   = "$PWD\bgserver\bgserver.exe"
python $BG health
```

## ★ 启动服务

服务有两种跑法，**优先用打包好的 exe**（不需要 Python 环境，双击即用）：

```powershell
# 方式一（推荐）：打包好的 exe。从 Releases 解压出来的目录里：
Start-Process $EXE `
    -ArgumentList "--shot-dir","$WS\shots","--extra-shot-dir","$WS\_bgshots" `
    -WorkingDirectory "$WS"

# 方式二：直接跑源码（需要 Python 3.8+，零第三方依赖）
python "$PWD\bgserver.py" --shot-dir "$WS\shots" --extra-shot-dir "$WS\_bgshots"
```

**启动时带上 `--shot-dir`，指向当前工作区的截图目录**（见下）。
两种跑法效果一样：无控制台窗口，托盘出现一个图标，服务以管理员权限常驻
（会弹一次 UAC，点「是」）。之后所有 `bgclient.py` 调用都复用它，
**不会再弹 UAC、不用重复启动**。

### ★ agent 自己启动会失败 —— 让用户来（实测）

**在受限沙箱 / 受管环境里，agent 起不来这个服务。** 服务启动时会走
`ShellExecuteW("runas")` 提权重启自己，而这一跳在受限环境里会**静默失败**
（不弹 UAC、不留日志、进程随即消失）：

| 方式 | 受限沙箱内结果 |
|---|---|
| `Start-Process <exe>`（默认提权） | ❌ 进程起一下就没，日志**零新记录** |
| 同上 + 捕获 stdout/stderr | ❌ 短暂存活，**不监听 8765**，随后消失 |
| `<exe> --help` | ✅ exit 0（exe 本身没坏） |
| `<exe> --no-elevate --no-tray` | ✅ **能起、能监听、日志正常** |

结论：**不是 exe 的问题，是提权那一跳被沙箱掐死。**
（这和 `bgserver.py` 源码里注释过的坑同源：`ShellExecuteW("runas")` 会返回成功
但实际什么都没启动。）

**所以 agent 的正确做法**：

1. `python $BG --no-autostart health` 探活（exit 0 已在跑 / exit 3 没起）；
2. 没起时**不要反复重试启动**，直接把启动命令交给用户，让他在**普通 PowerShell** 里跑：

```powershell
Start-Process $EXE `
    -ArgumentList "--shot-dir","$WS\shots","--extra-shot-dir","$WS\_bgshots" `
    -WorkingDirectory "$WS"
```

3. 用户起好后 agent 再用 `health` 确认，然后继续干活。

> ⛔ 别为了「自己搞定」去试 `--no-elevate` 凑合 —— 那样服务是 Medium 权限，
> 发不进管理员窗口（UIPI），很多目标点不动，等于白起。

### 想给用户一个一键启动脚本

仓库不自带启动脚本（各人路径不同），需要的话照下面写一个 `.cmd` 放桌面。
脚本应该在启动前做三件事，出问题明确报出来（不要默默失败）：

| 检查 | 建议报错文案 |
|---|---|
| exe 在不在 | `[X] Service executable not found:` |
| **`bgkit\` 在不在**（跑源码时） | `[X] Package directory missing:` ← 只拷 `.py` 没拷 `bgkit\` 时 |
| 服务是不是已经在跑 | `[i] The service is ALREADY running.` |

起来之后**打印一遍 `health` 输出**（版本 / 权限 / `uia.available`），
这样一眼能确认托盘上那个图标到底是哪个 build。

**写它的时候必须遵守**：

| 要求 | 原因 |
|---|---|
| ★ **必须存成 GBK（cp936）** | 双击时控制台是 936 代码页；**UTF-8 编码的 .cmd 在 cp936 下读不出中文路径**（实测 `LONG_FAIL`），会静默走兜底 |
| **行尾必须 CRLF** | 用 LF 时 cmd 解析多字节字符会错位，把 `echo` 读成 `cho`、中文当命令执行 |
| 不要写 8.3 短名兜底 | 见下：短名会污染服务白名单，反而制造新 bug |
| echo 文案用英文 | 中文 echo 在代码页不匹配时是乱码，用户看不懂 |

> ⚠️ **测试这类脚本必须在 cp936 下测**（`cmd /c "chcp 936 >nul && script.cmd"`）。
> 在 UTF-8 的 PowerShell 里测会得到**相反的错误结论** —— 第一版就是这么写错的：
> 当时测出「UTF-8 无 BOM + CRLF 最好」，实际双击时是 cp936，中文路径根本读不出来。

### ★ 短名会毒化截图白名单（实测踩过）

**不要在启动参数里用 8.3 短名**（`D:\LONGNA~1` 这种），哪怕是「兜底」也不行。

服务把 `--shot-dir` 存成绝对字符串后，用 `os.path.commonpath` 做**字符串前缀比较**
来判断路径是否越界。短名和长名是**不同的字符串**，于是：

| `--out` 写法 | 结果 |
|---|---|
| 长名 `$WS\shots\x.png` | ❌ **被拒**：路径越界（`code: 2`） |
| 短名 `D:\LONGNA~1\shots\x.png` | ✅ 成功 |
| 相对 `x.png` | ✅ 成功（服务内部拼的就是短名） |

**症状**：`shot --out "<长绝对路径>"` 报「截图路径越界」，但 `--out x.png` 正常。
**排查**：故意越界一次，报错里会列出实际白名单：

```powershell
python $BG --no-autostart shot --title "x" --out "C:\x.png"
# error 里会打印「只允许写进这些目录下: ...」
```

**修法**：停掉服务，用**长路径**重新启动（agent 起不了，见上文「agent 自己启动会失败」，
交给用户）。长名启动后白名单就是长名，绝对路径也能用了。

### ⛔ 截图默认不许落在用户主目录

**不要把截图存到 `%USERPROFILE%\.bgclick`**（那是**状态目录**：token / 端口 / 日志）。

* 状态目录是服务的**内部数据**，不是你放产出的地方；
* 往那儿写图会跟 token、日志混在一起，用户翻起来很痛苦；
* 它确实在「允许目录」里（服务启动时自动加），但**能写 ≠ 该写**。

**截图一律落在工作区内**。约定路径（`$WS` = 当前会话工作区，例如 `D:\my-project`）：

| 用途 | 落点 |
|---|---|
| 默认（相对 `--out`） | `$WS\shots\` |
| 临时/中间过程图 | `$WS\_bgshots\` |

启动服务时就把这两个目录加进去（上面的命令已经带了）。
如果服务已经在跑、且没带 `--shot-dir`，**先探活看它的实际落点**：

```powershell
python $BG health                       # 看 version / 权限
Get-Content "$env:USERPROFILE\.bgclick\bgserver.log" | Select-String '截图目录'
#   → 日志里会打印实际允许目录，确认 $WS\shots 在不在里面
```

不在里面时，要么请用户重启服务（agent 不要自己去关服务，见下文），
要么这次截图显式写**工作区内的绝对路径**（前提是该目录已在允许列表里）。

### ⛔ 两条禁止

| 禁止 | 原因 |
|---|---|
| 在有服务在跑时又去起一个 | 单实例锁会挡住，但会留下"端口被占但探活失败"这种难查状态 |
| 依赖 `bgclient.py` 的**自动拉起** | 拉起来的是源码不是 exe；受管环境里还可能被回收，导致每条命令重启一次服务（见下） |

**agent 的正确姿势** —— 先探活，没起才去起服务：

```powershell
python $BG --no-autostart health    # exit 0 = 已在跑，直接用；exit 3 = 没起
```

没起时再去启动服务；**不要**图省事直接 `python $BG windows` ——
那样会走自动拉起（见下一节）。

服务起来后，agent 在任意 PowerShell 里都能直接用：

```powershell
python $BG windows
python $BG shot  --title "记事本" --out note.png       # → $WS\shots\note.png
python $BG click --title "记事本" --pos 400,300
python $BG text  --title "记事本" --text "hello" --enter
python $BG key   --title "记事本" --keys ctrl+s            # 键盘组合键
python $BG mouse --title "记事本" --delta 0,-300           # 向上滑动
```

> ⛔ **要关服务时，不要自己关** —— 向用户说明需求，由用户操作。
> 详见下面「关闭服务：交给用户」一节。

> ⚠️ onedir 形式：exe 必须和同目录的 `_internal\` 一起。移动要整个文件夹搬。
> ⚠️ 重复启动不会起第二个实例（单实例锁 + 端口检查），只会提示"已有服务在运行"。
> ⚠️ 无窗口 exe 不打印错误，出问题看日志：`%USERPROFILE%\.bgclick\bgserver.log`

### 截图落在哪：只在工作区内，别碰 `.bgclick`（★ 实测踩过）

允许目录 = `--shot-dir` + 所有 `--extra-shot-dir` + 状态目录 `%USERPROFILE%\.bgclick`。
但**相对路径的 `--out` 只会解析到 `--shot-dir`**（列表里的第一个），
`--extra-shot-dir` 只是**额外放行**、不会成为默认落点：

```powershell
# ✅ 相对 --out 的落点由 --shot-dir 决定（相对路径按启动时的 cwd 解析）
#    启动时就按硬规则带上它，图自然落在工作区：
python $BG --no-autostart shot --title "记事本" --out note.png
#   → $WS\shots\note.png

# ✅ 或者显式给工作区内的绝对 --out（必须在允许目录内）
python $BG --no-autostart shot --title "记事本" \
    --out "$WS\_bgshots\probe.png"
```

⛔ **不要写 `%USERPROFILE%\.bgclick\xxx.png`**。它虽然在允许列表里，
但那是**状态目录**（token / 端口 / 日志），不是产出目录 ——
图混进去会让用户翻日志时一脸问号。**能写 ≠ 该写。**

⚠️ **服务没带 `--shot-dir` 时**，默认值是 `shots`，按**启动时的 cwd** 解析 ——
双击 exe 就是 exe 所在目录，图会落到
exe / 源码所在目录的 `shots\`（不是工作区）。
遇到这种情况，先确认实际落点：

```powershell
Get-Content "$env:USERPROFILE\.bgclick\bgserver.log" | Select-String '截图目录'
```

想让图进工作区，请用户重启服务并带上 `--shot-dir`（agent 不要自己关服务）。
越界会被拒（`code: 2`）。

其它启动参数：`--port 8765` / `--no-tray`（纯后台无图标）/ `--no-elevate`（不提权，
很多窗口会点不动）/ `--max-clicks 10000` / `--verbose` / `--open-log`。

## 兜底：客户端自动拉起（★ 别主动用）

`bgclient.py` 发现服务连不上时，会自己拉 `bgserver.py`（**源码**，弹一次 UAC），
并把「当前工作目录\shots」加进允许目录。

```powershell
python $BG windows      # ⚠️ 走的就是这条路，拉起来的是 py 不是 exe
```

**这不是日常启动方式**，只是「exe 缺失 / 用户没手动起」时的兜底。它的问题：

* 拉的是**源码**而不是打包好的 exe —— 没装 Python / 缺依赖时会失败；
* 受管沙箱里起的进程可能随命令结束被回收，于是**每条命令都可能重启一次服务**。

真要自动拉起，也请**先把服务起好**；服务已在跑时 `bgclient.py` 会直接复用，
不会再多起一个 —— 两条路**共用**同一个状态目录、同一个端口(8765)、同一个单实例锁。

## 1. 找窗口

```powershell
python $BG windows              # 列出所有可见窗口，带 ✔/✘
python $BG windows --only-postable
python $BG windows --human      # 人类可读
```

每个窗口带一个标记：

- **✔ `postable: true`** —— 能后台点击
- **✘ `postable: false`** —— 被 UIPI 拦截，见下面「权限」

定位方式（可组合）：

```
--title "记事本"          标题子串匹配（默认，不区分大小写）
--title "记事本" --exact   完全匹配
--regex                   标题按正则
--process notepad.exe     按进程名
--hwnd 0x00123456         直接给句柄
--index 1                 匹配到多个时选第几个
```

## 2. 操作

```powershell
# 点击客户区坐标 (400, 300)
python $BG click --title "记事本" --pos 400,300

# 点客户区正中心，连点 3 次，每次间隔 1 秒
python $BG click --title "记事本" --center --count 3 --interval 1.0

# 截图（相对路径落在服务的截图目录下）
python $BG shot --title "记事本" --out note.png

# 只截客户区（不含标题栏边框，方便量坐标）
python $BG shot --title "记事本" --out probe.png --client-only

# 输入文本（纯 WM_CHAR 通道，中文/emoji 用这个）
python $BG text --title "记事本" --text "hello" --enter

# 键盘：组合键 / 多键同按 / 逐字符（走 WM_KEYDOWN，不是 WM_CHAR）
python $BG key --title "记事本" --keys ctrl+shift+s
python $BG key --title "记事本" --keys shift+a --repeat 3
python $BG key --title "记事本" --hold ctrl+shift+a --hold-seconds 1.5
python $BG key --title "记事本" --type "Hello!"

# 鼠标滑动 / 拖拽 / 滚轮
python $BG mouse --title "记事本" --delta 0,-500            # 向上滑 500 像素
python $BG mouse --title "记事本" --to 400,300
python $BG mouse --title "记事本" --path "100,100;300,300"
python $BG mouse --title "记事本" --pattern circle --distance 200
python $BG mouse --title "记事本" --scroll -5               # 向下滚 5 格
python $BG mouse --title "记事本" --from 200,400 --delta 0,-300 --drag   # 按住拖动

# 右键点击：--button right（中键 middle）
python $BG click --title "记事本" --pos 400,300 --button right

# 单窗口详情 / 探测能否投递消息
python $BG window --title "记事本"
python $BG probe  --title "记事本"

# ⛔ 关闭服务：不要自己关，向用户说明需求（见下面「关闭服务」一节）
```

### 键盘（`key`）—— 和 `text` 的区别

| 命令 | 走哪条路 | 适合 |
|---|---|---|
| `text` | 逐字符 `WM_CHAR` | 编辑框、输入框；**中文/emoji 只能走它** |
| `key` | `WM_KEYDOWN/UP` + 规范 lParam | 菜单快捷键、IDE、游戏、画布控件 |

`key` 的三种玩法（互斥）：

```powershell
--keys ctrl+shift+s      # 组合键，可重复给，依次发
--hold ctrl+shift+a      # 多键同按（按住不放），配 --hold-seconds
--type "Hello!"          # 逐字符输入
```

按键名：`ctrl` / `shift` / `alt` / `win` + 主键。主键可以是 `a-z`、`0-9`、
`f1`-`f24`、`enter`、`esc`、`tab`、`space`、`backspace`、`delete`、`home`、`end`、
`pageup`、`pagedown`、`up`/`down`/`left`/`right`、`plus`、`minus`、`comma`、`period`，
也可以直接写虚拟键码 `0x41` 或 `65`。想按加号键本身要写 `ctrl+plus`
（`+` 是组合键分隔符）。

> ★ **`--type` 默认每个字符只走一条通道**，用 `WM_CHAR` 送精确字符。
> 早期版本两条通道都发，在记事本里会打出 `Hheelllloo!1`（字符翻倍 + 大写变小写）——
> 已修复。若目标是只认虚拟键码的程序，加 `--as-keys`。

### 鼠标滑动（`mouse`）

```powershell
--delta 0,-500                              # 相对位移（正数向右/向下）
--to 400,300                                # 滑到绝对客户区坐标
--path "100,100;300,300;500,200"            # 途经点
--pattern up|down|left|right|circle|square|zigzag --distance 300
--scroll -5 [--axis horizontal]             # 滚轮
--from 200,400 --delta 0,-300 --drag        # 按住拖动（滑块、列表、地图）
```

手感旋钮：`--steps 30`（插值步数）、`--delay 0.02`（每步间隔）、
`--duration 1.2`（整段总时长）、`--ease`（平滑加减速）、`--hold 0.03`（按下/抬起停顿）。

> **为什么滑动要插值**：拖动类交互靠**连续 `WM_MOUSEMOVE`** 累积。不补点，
> 程序看到的是「按下 → 瞬移到终点 → 抬起」，要么当没发生，要么判成 0 距离。
> 拖拽每一步还必须带按键状态位（`MK_LBUTTON` 等），否则程序认为中途松手。

### ★ 滑动/点击没反应？按这个顺序换方式

鼠标位置在 Windows 里是**全局状态**，程序随时能 `GetCursorPos` 查真实光标。
所以现代程序（浏览器/Electron、游戏、自绘 UI）收到 `WM_MOUSEMOVE` 后会核对真实位置，
发现没动就当这条消息不存在。**「点击有反应、滑动没反应」是正常现象，不是 bug**——
点击的「按下」是**事件**，程序认；「移动」是**状态**，状态没法用消息伪造。

| 方式 | 走哪条路 | 动真实光标 | 什么时候用 |
|---|---|---|---|
| `post`（默认） | 窗口消息队列 | 否 | 后台不打扰，普通程序 |
| `send` | 同步投递 | 否 | 忽略 post 的程序 |
| `hardware` | `SetCursorPos` + `mouse_event` | **是** | ⛔ 滑动时需用户明确同意 |
| `sendinput` | `SendInput`（系统输入队列） | **是** | ⛔ 滑动时需用户明确同意 |

```powershell
# agent 默认只能用这两条（都不动光标）
python $BG mouse --title "xxx" --delta 0,-200
python $BG mouse --title "xxx" --delta 0,-200 --drag --method send
```

**真输入那两条（`hardware` / `sendinput`）agent 不要自己跑** —— 先报告 + 问用户。
（例外：键盘的 `key --method sendinput` 是全局按键，也会打到前台窗口，同样先问；
键盘的 `post`/`send` 不受限。）

代价（要跟用户说清楚）：真输入**会占用真实光标，属于前台行为**；
滚轮发给**光标底下那个窗口**，所以会先把光标挪到位；默认结束后把光标放回原处
（`--no-restore-cursor` 可关）；目标窗口必须可见、没被完全遮住。

> **关于「扫描码」**：那是**键盘**概念。`MOUSEINPUT` 的字段只有
> `dx/dy/mouseData/dwFlags/time/dwExtraInfo`，没有任何扫描码字段。
> 键盘侧扫描码重要是因为 VK 随布局/修饰键变、扫描码不变 ——
> 只读底层键盘输入的程序（DirectInput/Raw Input 类）对 VK 不买账、对扫描码买账。
> 鼠标侧的对应物是 `SendInput` 的 `MOUSEEVENTF_MOVE|ABSOLUTE|VIRTUALDESK`，不叫扫描码。
>
> **能力边界**：`SendInput` 注入仍会被 Raw Input 识别（`hDevice=NULL`，
> 低级钩子有 `LLMHF_INJECTED`）。要「像真实硬件」只有驱动级虚拟 HID，
> 本工具不做。

### ⛔ 关闭服务：交给用户，别自己动手

**agent 不要执行 `shutdown`，也不要 `taskkill` 去关服务。** 需要关的时候，
用一句人话把需求告诉用户，由用户自己关。例如：

> 服务现在还在后台跑着（PID `<pid>`，管理员权限）。要关掉的话，
> 任务栏 `^` 折叠区右键那个图标选「退出」就行 —— 我这边没权限动它。

原因有两条，都很硬：

1. **`shutdown` 接口在托盘模式下关不干净**（源码缺陷，已确认）：
   它只停 HTTP 线程，主线程还阻塞在托盘消息循环里，进程活着、8765 端口继续占着，
   但返回值是 `{"ok": true}` —— 只看返回值会被骗。
2. **agent 大概率没权限**（Access denied）：服务是管理员 / High 完整性级别，
   agent 的终端通常是 Medium，`Stop-Process` / `taskkill` 会被拒。

**用户的关法**（写给你去告诉用户，不是你去执行）：

| 场景 | 做法 |
|---|---|
| 有托盘图标 | 任务栏 `^` 折叠区 → 右键图标 → **退出**（走 `do_shutdown`，能清干净） |
| 没图标 / 卡住 | 管理员 PowerShell：`taskkill /PID <pid> /F`（PID 从 `python $BG health` 拿） |

用户关完，agent 可以用 `python $BG --no-autostart health` 确认变成 exit 3，再起新的 exe。

> 客户端里 `python $BG shutdown` 这个子命令仍然存在，但**别用** —— 除了上面两个问题，
> 它还会让「端口被占但探活失败」这种最难查的状态出现。
> 想从根上避开：用 `--no-tray` 启动服务，无托盘模式下 `shutdown` 能真正结束进程。

`click` 的其它旋钮：`--button left|right|middle`、
`--method post|send|hardware|sendinput`、
`--jitter 3`（像素抖动）、`--activate`（点击前调到前台）、`--force-continue`、
`--screen-pos X,Y`（屏幕绝对坐标）、`--no-restore-cursor`（真输入后不还原光标）。

`shot` 的其它旋钮：`--format png|bmp`（默认 png）、
`--method auto|print|bitblt`（抓图方式，`auto` = 先试 `PrintWindow`，失败回退 `bitblt`）、
`--inline`（结果里附 base64 图，想直接看图而不落盘时用）。
不给 `--out` 时自动命名成 `<进程名>_<hwnd>.png`，落在服务的截图目录
—— 服务按硬规则启动的话就是 `$WS\shots\`。

## 3. UIA 元素查询（按元素名拿坐标，不用数像素）

系统自带 UI Automation 读窗口里的元素树，按名字/控件类型查元素、直接拿坐标。
**纯 ctypes 实现，没用 comtypes / uiautomation 包**（细节见 `bgkit\bgclick\uia.py` 顶部）。

```powershell
# 看元素树（--limit 内先到先得，具名控件常在深层，找不到就加条件筛）
python $BG uia --title "QQ"
python $BG uia --title "QQ" --interactive-only          # 只要能点的

# 按条件查
python $BG uia --title "QQ" --name "发送"                # 元素名（子串，不分大小写）
python $BG uia --title "QQ" --type Edit                  # 控件类型
python $BG uia --title "QQ" --automation-id "okBtn"
python $BG uia --title "QQ" --name "发送" --name-exact    # 完全匹配
```

返回里每个元素带 `rect`（屏幕坐标）、`center`、**`client_center`（客户区坐标）**——
后者直接就是 `click --pos` 要的那个坐标系。

不经服务的独立入口（`bgclick.py`，UIA 完全不依赖 bgserver）：

```powershell
python $CLICK --title "QQ" --uia-walk                  # 列元素树
python $CLICK --title "QQ" --uia-find  --uia-name "发送"  # 只查不点
python $CLICK --title "QQ" --uia-click --uia-name "发送"  # 查到就用它点
```

`bgclick.py` 侧开关：`--uia-walk` / `--uia-find` / `--uia-click`、`--uia-name` /
`--uia-type` / `--uia-id` / `--uia-class` / `--uia-exact` / `--uia-interactive` /
`--uia-max-depth`（默认 12）/ `--uia-limit`（默认 500）/ `--uia-index` / `--uia-tree`。

### 三条必须记住的

| 事实 | 说明 |
|---|---|
| **UIA 找到元素 ≠ 程序响应了点击** | 和 `--method post` 一回事：投递成功只代表消息进了队列。**准备做不可逆动作前、任务结束时**截图看（见「什么时候该截图确认」）。 |
| **超限返回的是部分结果，不是报错** | 元素超过 `--uia-limit`（默认 500）时置 `truncated=true` 并照常返回。UIA 碰上几千行的列表会把目标程序卡住，这个上限必须留着。 |
| **默认只读，不会误触** | `uia` / `--uia-find` 都是纯查询。只有 `--uia-click`（或 `bgclick.py --uia-click`）才真的点。 |

### 已知限制（实测）

* **Electron 程序（QQ NT / VS Code / Discord…）树很浅**：可能只有一堆嵌套 `pane`，
  正文和按钮拿不到名字。**但 QQ 实测能拿到**：输入框是 `Edit`、发送按钮是 `Button`，
  所以「Electron 一定不行」是错的 —— 先 `uia` 看一眼再下结论。
* **树大时查询会慢**：QQ 完整树约 365 个节点、单次查询 3~4 秒。加 `--type` / `--name`
  缩小范围不会更快（过滤发生在遍历之后），想快只能调小 `--max-depth`。
* **`--uia-limit` 小的时候会先拿到容器**：`pane` 占满预算，具名控件被挤出结果。
  遇到"只有 pane"就调大 `--limit` 或者直接按名字查。

## 坐标怎么定（重要）

> ★ **先试 UIA（上一节 §3），它不用截图。** 按元素名查一下就能拿到 `client_center`，
> 那是现成的客户区坐标。只有 UIA 查不到那个元素（自绘 UI / 元素没名字）时，
> 才回到下面这套量像素的老办法。

`--pos X,Y` 是**相对客户区左上角**的像素偏移，不含标题栏和边框。

1. `shot --client-only --out "$WS\_bgshots\probe.png"` 抓纯客户区图（放临时目录，别污染 `shots\`）
2. **把图读出来看**（用 `read_image`），量出目标按钮的像素位置
3. 拿那个坐标去 `click --pos`

`--center` 可以点正中心，用来快速排除"是不是坐标错了"。不确定时先用 `--center` 试。

## ⚠️ 什么时候该截图确认（不是每次都截）

**`PostMessageW` 返回成功 ≠ 程序会响应** —— 这只代表操作系统收下了消息。

但**不要每步操作都截图**。截图很贵（每次一张 PNG，还要看图），
连续操作里逐步截图纯属浪费。**只在两个时机截**：

| 时机 | 为什么 | 例子 |
|---|---|---|
| ★ **即将产生不可逆影响之前** | 截错了就发出去了，收不回来 | 准备点「发送」、提交表单、删除、覆盖保存 |
| ★ **任务结束时** | 给用户一个「做成了」的证据 | 消息已发出、文件已保存、界面已切换 |

**中间的每一步都不截**。点击输入框、逐字输入、切换标签页这类可逆动作，
直接连着做，别穿插截图。

```
点击输入框 → 输入文本 → [截图] 确认无误 → 点发送 → [截图] 确认已发出
                         ↑ 发之前这一次很关键        ↑ 结束这次给证据
```

### 但「怀疑没生效」时，随时可以截

上面是**默认节奏**，不是禁令。出现下面任一情况，立刻截一张看 ——
这时候省那一次截图是捡了芝麻：

* 投递返回失败，或返回 `ok` 但逻辑上说不通；
* 目标是 Electron / 自绘 UI / 游戏这类**已知对合成消息挑剔**的程序（见「已知限制」）；
* 刚换了一个从没操作过的窗口，第一次拿不准它吃不吃消息；
* 用户说「怎么没反应」。

> 判断标准就一条：**这次截图的收益（避免一次真损失 / 确认一个未知）大过它的成本吗？**
> 大就截，不大就跳过。中间步骤通常不大。

另外：**返回 `ok: true` 不等于程序理了你**（尤其组合键 —— 实测 QQ 里
`ctrl+a` 返回 4 条事件全 `ok`，但完全没有选中效果）。所以「准备发消息」前那一次
截图**别省**，它正是用来兜住这种情况的。

## 权限（点不动时看这里）

Windows 的 UIPI 规则：**只有完整性级别(IL) 不低于目标的进程才能给目标窗口发消息**，
否则消息被静默丢弃（错误码 5）。

| 进程类型 | 完整性级别 |
|---|---|
| 普通程序 | Medium |
| 管理员提权后 | High |
| 受限 / 沙箱终端 | **Low ← 最大的坑** |

**最关键的一条**：如果服务跑在 **Low**，它**发不进任何普通程序** ——
跟目标是谁无关，**提权也救不了**。必须换一个普通终端启动服务。

判断方法：

```powershell
python $BG health --human
```

看 `integrity` 字段。是 `Low（低）` 就换终端，别浪费时间试。

## 点不动 / 滑不动的排查顺序

按这个顺序，基本能解决：

1. **服务是不是 Low IL** → `health` 看 `integrity`
2. **窗口是不是 ✘** → `windows` 看 `postable`
3. **坐标对不对** → 用 `--dry-run` 看实际命中哪个窗口，或用 `--center` 排除
4. **点在子控件上** → 工具会逐层探测、自动选最深的可投递窗口，通常不用管
5. **程序不吃合成消息** → 换 `--method send`
6. **需要窗口激活才响应** → 加 `--activate`
7. **到这一步就停** —— 剩下的路（`hardware` / `sendinput`）都要抢用户的光标。

**第 7 步不是「继续试」，是「报告 + 问」**：

```powershell
python $BG health --human            # 确认服务和权限状态，用于报告
python $BG shot --title "xxx" --out "$WS\_bgshots\after.png"   # 截图作为证据
```

然后告诉用户：合成消息这条路对这个窗口不生效；还有真输入可选，
**用不用由用户决定**（见开头的硬规则）。

> ⛔ 不要为了「一次把问题解决」就自己跑 `mouse --method hardware`。
> 用户可能正在打字、开会或者拖文件 —— 抢走鼠标的代价由他承担，得他同意。

更细的诊断（不需要服务，直接跑库里的 CLI）：

```powershell
python $CLICK --scan                       # 哪些窗口能点，附自己的 IL
python $CLICK --doctor --title "记事本"     # 逐层探测卡在哪一层
python $BG probe --title "记事本"                           # 含双方 IL
```

## 已知限制（别浪费时间的地方）

* **合成消息天生无效的目标**：DirectX/Vulkan 独占全屏游戏、用 Raw Input 自己读鼠标的软件、
  Chrome/Electron 的部分区域。这些只能上 `--method hardware` / `sendinput`
  （⛔ 滑动的那两条要用户明确同意才能用，见开头硬规则）。
* **鼠标「移动」比「点击」更容易失效**：位置是全局状态，程序会查真实光标；
  点击是事件，程序通常照单全收。所以「点击能行、滑动不行」是正常现象，不是 bug。
* **`--method hardware` / `sendinput` 会占用用户的真实光标**，属于前台行为，
  所以永远是显式选择，不会被自动切过去。过程中别让用户用鼠标。
  两者都不支持 `--count 0` 无限循环时的光标恢复。
* **`sendinput` 仍可被识别为注入**（`RAWINPUTHEADER.hDevice == NULL`、
  低级钩子 `LLMHF_INJECTED`）。要绕过反作弊只有驱动级方案，本工具不做。
* **`PrintWindow` 不一定成功**。看返回的 `capture_method`：
  - `printwindow-fullcontent` / `printwindow` → 真后台抓图，遮挡也能抓 ✅
  - `bitblt` → 回退到屏幕抓图，**被遮挡的部分会是黑的** ⚠️
* **最小化窗口**截出来可能是黑图，先让用户还原。
* **截图路径越界会被拒**（`code: 2`）。相对 `--out` 落在 `--shot-dir`（默认 `shots`，
  按服务启动时的 cwd 解析），不是 agent 的 cwd —— 拿不准就写绝对路径，且必须在允许目录内。
* ⛔ **截图只落工作区**（`$WS\shots\` 正式产出、`$WS\_bgshots\` 临时过程图）。
  **不要写 `%USERPROFILE%\.bgclick\`** —— 那是状态目录（token/日志），不是产出目录。

## 全局修饰符（★ 写在子命令**之前**）

```powershell
python $BG --host 127.0.0.1 --port 8765 --token <TOKEN> --no-autostart health
python $BG --no-autostart windows
```

| 修饰符 | 说明 |
|---|---|
| `--host HOST` | 服务地址（默认 `127.0.0.1`） |
| `--port PORT` | 端口（默认读状态目录的 `port.txt`，没有则 8765） |
| `--token TOKEN` | 覆盖 Bearer token（默认读状态目录的 `token.txt`；`health` 用不上它） |
| `--no-autostart` | 服务没跑时不自动拉起（默认会自动拉起并弹 UAC） |
| `--human` | 人类可读输出，替代 JSON |

> ⚠️ **位置有讲究（实测踩过）**：前四个只注册在**主命令**上，必须写在子命令**之前**。
>
> ```powershell
> python $BG --no-autostart health    # ✅ exit 3，正常报"服务没启动"
> python $BG health --no-autostart    # ❌ exit 2，unrecognized arguments
> ```
>
> 唯一的例外是 `--human`：主命令和每个子命令都注册了它，前后都行
> （`python $BG --human windows` 和 `python $BG windows --human` 等价）。

`--no-autostart` 在调试脚本时特别有用 —— 避免每条命令都偷偷拉起服务弹一次 UAC。

## 退出码（脚本化时用）

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 未找到窗口 / 服务端错误 |
| 2 | 参数错误 |
| 3 | 服务未启动 / 连不上 |
| 4 | 权限被拒（UIPI / token） |

输出默认就是 JSON（`{"ok": true/false, ...}`），失败带 `error` 字段，从不甩 traceback。
加 `--human` 换人类可读。

## 状态目录

token / 端口 / pid / 日志存在**状态目录**里，解析顺序（客户端与服务端同一套）：

1. 环境变量 `BGCLICK_STATE_DIR`
2. `%USERPROFILE%\.bgclick`
3. `%TEMP%\bgclick-state`

想固定就设环境变量：

```powershell
$env:BGCLICK_STATE_DIR = "D:\some\writable\dir"
```

客户端自动拉起服务时会把 `BGCLICK_STATE_DIR` **显式传过去** —— 不这么做两边会各算各的目录，
token 对不上，请求全 401（实测踩过）。

## 安全边界（不要绕过）

服务跑在管理员权限下且能模拟鼠标键盘，是一块提权面。已内置：

- 只监听 `127.0.0.1`（局域网/外网连不上）
- 强制 Bearer token（`%USERPROFILE%\.bgclick\token.txt`）
- 校验 `Host` 头（防 DNS rebinding）
- 截图路径限制在允许目录内（默认调用方的 `./shots`）
- 单次点击次数上限

**不要**为了绕过限制去改代码里的检查，用命令行参数放开
（`--max-clicks`、`--extra-shot-dir`）。

## 直接调 HTTP API

需要绕过 CLI 直接发请求时，完整接口文档在同目录 `README.md`。
基址 `http://127.0.0.1:8765`，除 `/health` 外都要
`Authorization: Bearer <token>`。

v1.5.0 的端点是：`/health`、`/windows`、`/window`、`/probe`、`/click`、
`/text`、**`/key`（键盘）**、**`/mouse`（滑动/拖拽/滚轮）**、`/screenshot`、
**`/uia`（UIA 元素查询）**、`/shutdown`。

`/uia` 收 `name` / `control_type` / `automation_id` / `class_name` / `name_exact` /
`interactive_only` / `max_depth` / `limit`，GET 和 POST 都行。不给任何查找条件
就是遍历整棵树（等价于 `--uia-walk`）。
