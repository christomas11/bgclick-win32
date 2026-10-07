# bgclick-win32

**后台操作 Windows 窗口：点击 / 键盘 / 滑动 / 截图，默认不抢光标、不抢焦点。**

用 `PostMessageW` 把鼠标键盘消息**投递**进目标窗口的消息队列 —— 你在前台干自己的活，
它在后台点它的。截图用 `PrintWindow` 让窗口自己画自己，被别的窗口盖住也能抓到。

纯标准库，**零第三方依赖**（PNG 编码都是自己拿 zlib 写的）。Python 3.8+，实测 3.12.4。

> 当前版本 **v1.5.0**。
>
> - 想用 AI agent 驱动它？看 [`AGENTS.md`](AGENTS.md) —— 给 agent 的操作手册。
> - 命令速查看 [`docs/常用指令.md`](docs/常用指令.md)。

---

## 它解决什么问题

自动化一个桌面程序，常规做法是 `SetCursorPos` + `mouse_event`（或 pyautogui 那一套）——
代价是你的鼠标被征用，期间不能碰电脑，屏幕上光标自己乱跑。

本项目的默认路径是**消息投递**：把 `WM_LBUTTONDOWN` / `WM_MOUSEMOVE` / `WM_KEYDOWN`
直接塞进目标窗口的消息队列。光标不动、焦点不换，你可以同时用电脑。

代价也很实在（见「[已知限制](#七已知限制)」）：位置类操作容易被程序识破。
所以两条路都提供，按目标程序挑。

| 能力 | 走消息投递（默认，不动光标） | 走真输入（占光标，前台行为） |
|---|---|---|
| 点击（左/右/中键） | ✅ `post` / `send` | ✅ `hardware` / `sendinput` |
| 键盘（组合键 / 多键同按 / 逐字符） | ✅ `WM_KEYDOWN` 通路 | ✅ `sendinput`（含扫描码） |
| 鼠标滑动 / 拖拽 / 滚轮 | ✅ 插值成串 `WM_MOUSEMOVE` | ✅ `hardware` / `sendinput` |
| 文本输入（中文 / emoji） | ✅ `WM_CHAR` 通路 | — |
| 截图 | ✅ `PrintWindow`，遮挡也能抓 | — |
| **元素查询（UIA）** | ✅ 按名字/类型查元素、直接拿坐标 | — |

---

## 快速开始

```bash
git clone https://github.com/christomas11/bgclick-win32.git
cd bgclick-win32
```

不需要装任何东西，也不用 `pip install`。三个入口，别搞混：

| 文件 | 角色 | 什么时候用 |
|---|---|---|
| `bgclient.py` | 零依赖命令行客户端 | **日常就用这个**，一条命令一件事 |
| `bgserver.py` | 常驻 HTTP 服务的源码 / 打包入口 | `python bgserver.py` 起服务；或打包成 exe |
| `bgclick.py` | 核心 Win32 库 + 独立 CLI | 诊断（`--doctor` / `--scan`）、不经过服务直接用 |

```bash
# 1) 起服务（自动申请管理员权限，弹一次 UAC；之后常驻，不再反复弹）
python bgserver.py

# 2) 另开一个终端
python bgclient.py health                    # 探活：版本 / 是否管理员 / 完整性级别
python bgclient.py windows                   # 列出窗口，附「能不能投递」的 ✔/✘
python bgclient.py click --title "记事本" --pos 400,300
python bgclient.py shot  --title "记事本" --out note.png
python bgclient.py key   --title "记事本" --keys ctrl+shift+s
python bgclient.py mouse --title "记事本" --scroll -5
python bgclient.py shutdown
```

服务没起的时候 `bgclient.py` 会**自己拉起来**（会弹一次 UAC），
所以上面这些命令其实可以直接跑。

> `bgkit/` 是真正的实现包，`bgclick.py` / `bgclient.py` / `bgserver.py` / `bgtray.py`
> 只是十来 KB 的**兼容壳**（拆包前的老名字、老用法全部照旧）。
> 只拷那几个 `.py` 会直接 `ModuleNotFoundError`。

### 命令行速查

```bash
# 诊断：哪些窗口能点（✔/✘）、为什么点不动
python bgclick.py --list
python bgclick.py --scan
python bgclick.py --doctor --title "记事本" --pos 1,1

# 点击
python bgclick.py --title "记事本" --pos 400,300 --count 3 --interval 1.0
python bgclick.py --title "记事本" --center --count 0 --interval 5 --jitter 3
python bgclick.py --title "记事本" --pos 400,300 --button right

# 滑动 / 拖拽 / 滚轮
python bgclick.py --swipe --title "记事本" --delta 0,-500
python bgclick.py --swipe --title "记事本" --dx 0 --dy -500 --drag
python bgclick.py --swipe --title "记事本" --pattern circle --distance 200

# 截图
python bgclick.py --title "记事本" --shot shots/note.png
python bgclick.py --title "记事本" --shot shots/note.png --client-only
```

> `bgclick.py` 的 CLI **没有键盘参数**（键盘请走 `bgclient.py key`），
> 或者直接调库：`send_chord_input(parse_chord("ctrl+s"))`、
> `send_key_sequence(hwnd, [parse_chord("ctrl+s")])`。

`bgclient.py` 的完整子命令见 [`docs/常用指令.md`](docs/常用指令.md)。

---

## 一、原理：为什么能「纯后台」

不用 `SetCursorPos` + `mouse_event`（那是物理点击，会把鼠标从你正在干的活里抢走）。
默认用 `PostMessageW` 把消息**投递**进目标窗口的消息队列 ——
光标不动、焦点不变、前台完全无感。

截图用 `PrintWindow` —— 让窗口**自己**把自己画到内存 DC 上，所以被别的窗口盖住也能抓到。

### 两条路的取舍

**消息投递不是万能的**，而且失效方式有规律：

* **鼠标「移动」比「点击」更容易失效**。位置在 Windows 里是**全局状态**，
  程序随时能 `GetCursorPos` 查真实光标；现代程序（浏览器/Electron、游戏、自绘 UI）
  收到 `WM_MOUSEMOVE` 后会核对真实位置，发现没动就当这条消息不存在。
  而点击的「按下」是一个**事件**，程序通常照单全收。
  → 所以「点击有反应、滑动完全没反应」是正常现象，不是 bug。
* **键盘**：某些程序只读底层键盘输入，对虚拟键码不买账。

于是有三档输入方式：

| `--method` | 走哪条路 | 动真实光标 | 适合 |
|---|---|---|---|
| `post`（默认） | 窗口消息队列，异步 | 否 | 后台不打扰，普通程序 |
| `send` | 同步投递，等目标处理完 | 否 | 忽略 post 的程序 |
| `hardware` | `SetCursorPos` + `mouse_event` | **是** | 点击/键盘：post/send 没反应时；滑动：占光标 |
| `sendinput` | `SendInput` 系统输入队列 | **是** | 键盘扫描码、原子拖拽；滑动：占光标 |

`sendinput` 的关键优势是**整批原子提交**：拖拽的「按下 → 移动×N → 抬起」打包成一次
`SendInput` 调用，不会被用户真实的鼠标动作插队拆散。

> **关于「扫描码」**：那是**键盘**概念。`MOUSEINPUT` 的字段只有
> `dx / dy / mouseData / dwFlags / time / dwExtraInfo`，没有扫描码字段。
> 键盘侧扫描码重要是因为 VK 随布局/修饰键变、扫描码不变 ——
> `SendInput` + `KEYEVENTF_SCANCODE`（`wVk=0`，只交 `wScan`）能让只读底层键盘输入的
> 程序认账。鼠标侧的对应物是 `MOUSEEVENTF_MOVE|ABSOLUTE|VIRTUALDESK`，名字不叫扫描码。
>
> **能力边界**：`SendInput` 注入仍会被 Raw Input 识别
> （`RAWINPUTHEADER.hDevice == NULL`，低级钩子有 `LLMHF_INJECTED`）。
> 要「像真实硬件」只有驱动级虚拟 HID，本工具不做。

---

## 二、常驻服务（bgserver）

```bash
python bgserver.py                 # 启动（自动申请管理员权限，弹一次 UAC）
python bgserver.py --port 9000     # 换端口
python bgserver.py --no-elevate    # 不提权启动（很多窗口会点不动）
python bgserver.py --verbose       # 打印每个请求
```

启动后常驻后台，之后所有操作都复用它已有的管理员权限，**不再反复弹 UAC**。

| 启动参数 | 说明 |
|---|---|
| `--port 8765` | 监听端口（默认 8765） |
| `--shot-dir shots` | 截图**主目录**（相对 `--out` 都落这里） |
| `--extra-shot-dir D:\x` | 额外允许目录，可重复 |
| `--no-tray` | 不显示托盘图标（纯后台，`shutdown` 能真正退出） |
| `--no-elevate` | 不提权（受限沙箱里提权无效，程序会自动跳过） |
| `--max-clicks N` | 单次请求点击上限（默认 10000） |
| `--verbose` / `--open-log` | 打印每个请求 / 启动时打开日志窗口 |

截图只允许写进允许目录（`--shot-dir` + 所有 `--extra-shot-dir` + 状态目录），
越界会被拒（`code: 2`）。`--out` 的相对路径基于**服务的**截图目录，不是客户端的 cwd。

> `%USERPROFILE%\.bgclick` 虽然在允许列表里，但它是**内部数据目录**
> （token / 端口 / pid / 日志），别往里放截图。

### 客户端

```bash
python bgclient.py health                      # 探活
python bgclient.py windows                     # 列出窗口（✔/✘ 标注能否投递）
python bgclient.py windows --only-postable     # 只列能点的
python bgclient.py window --title "记事本"      # 单窗口详情
python bgclient.py probe --title "记事本"       # 探测能否投递消息

python bgclient.py click --title "记事本" --pos 400,300
python bgclient.py click --title "记事本" --center --count 3
python bgclient.py click --title "记事本" --pos 400,300 --button right
python bgclient.py shot  --title "记事本" --out note.png
python bgclient.py text  --title "记事本" --text "hello" --enter

# 键盘：组合键 / 多键同按 / 逐字符
python bgclient.py key --title "记事本" --keys ctrl+shift+s
python bgclient.py key --title "记事本" --hold ctrl+shift+a --hold-seconds 1.5
python bgclient.py key --title "记事本" --type "Hello!"

# 鼠标滑动 / 拖拽 / 滚轮
python bgclient.py mouse --title "记事本" --delta 0,-500
python bgclient.py mouse --title "记事本" --from 200,400 --delta 0,-300 --drag
python bgclient.py mouse --title "记事本" --scroll -5
```

退出码：`0` 成功、`1` 用法/未找到、`2` 参数错误、`3` 服务未启动、`4` 权限被拒。
输出默认是 JSON，加 `--human` 换人类可读。

#### key 子命令（键盘）

| 参数 | 说明 |
|---|---|
| `--keys CHORD` | 组合键，可重复给。如 `--keys ctrl+shift+s --keys f5` |
| `--hold KEYS` | 多键同按（按住不放），`--hold ctrl+shift+a` 或 `ctrl,shift,a` |
| `--type TEXT` | 逐字符输入（默认每字符只走一条通道） |
| `--repeat N` / `--interval` / `--hold-gap` / `--hold-seconds` | 重复次数 / 间隔 / 修饰键与主键间隔 / 按住时长 |
| `--method` | `post`（默认）/ `send` / `sendinput`（系统输入队列，**默认只发扫描码**） |
| `--no-scancode` | `sendinput` 时改用虚拟键码 |
| `--as-keys` / `--as-both` | `--type` 时全走按键通道 / 两条通道都发（后者会字符翻倍，慎用） |

按键名：`ctrl`/`shift`/`alt`/`win` + 主键；主键支持 `a-z`、`0-9`、`f1`-`f24`、
`enter`、`esc`、`tab`、`space`、`backspace`、`delete`、`home`、`end`、`pageup`、
`pagedown`、`up`/`down`/`left`/`right`、`plus`、`minus`、`comma`、`period`，
也可以直接写虚拟键码（`0x41` 或 `65`）。

> `key` 和 `text` 的区别：`text` 只发 `WM_CHAR`，只有编辑框认；
> `key` 走 `WM_KEYDOWN/UP`，菜单快捷键、IDE、游戏、画布控件吃这个。
> **中文 / emoji 只能用 `text`。**

#### mouse 子命令（滑动）

| 参数 | 说明 |
|---|---|
| `--delta DX,DY` | 相对滑动（正数向右/向下） |
| `--to X,Y` | 滑到绝对客户区坐标 |
| `--path "x,y;x,y"` | 途经点 |
| `--pattern 名字` | `line`/`up`/`down`/`left`/`right`/`circle`/`square`/`zigzag` |
| `--scroll N` / `--axis` | 滚轮格数 / 方向轴 |
| `--from X,Y` | 起点（默认客户区中心） |
| `--drag` / `--button` / `--no-release` | 按住拖动 / 键位 / 拖完不抬键 |
| `--steps` / `--delay` / `--duration` / `--ease` | 手感旋钮 |
| `--method` | `post`（默认）/ `send` / `hardware` / `sendinput` |
| `--no-batch` | `sendinput` 时逐点提交而非整批 |
| `--no-restore-cursor` | 真输入后不还原光标 |

---

## 三、UIA 元素查询：按元素名拿坐标，不用数像素

**v1.5.0 新增。** 老办法是截图 → 用眼睛量像素 → 换算客户区坐标，又慢又不准。
现在可以用系统自带的 **UI Automation** 读窗口里的元素树，按名字/控件类型找元素，
直接拿到坐标。

```bash
# 看元素树（默认只读，不会误触）
python bgclient.py uia --title "QQ"
python bgclient.py uia --title "QQ" --interactive-only     # 只要能点的

# 按条件查
python bgclient.py uia --title "QQ" --name "发送"           # 元素名（子串，不分大小写）
python bgclient.py uia --title "QQ" --type Edit             # 控件类型
python bgclient.py uia --title "QQ" --automation-id "okBtn"
python bgclient.py uia --title "QQ" --name "发送" --name-exact
```

每个元素返回 `rect`（屏幕坐标）、`center`、**`client_center`（客户区坐标）** ——
后者直接就是 `click --pos` 要的那个坐标系。查到坐标再交给 `click`，那条路依然不抢光标。

```bash
# 查到坐标 → 点它（两步都走消息投递，光标全程不动）
python bgclient.py uia  --title "QQ" --name "发送"
python bgclient.py click --title "QQ" --pos <client_center>
```

也可以不经服务直接用 `bgclick.py`（UIA 完全不依赖 bgserver）：

```bash
python bgclick.py --title "QQ" --uia-walk                  # 列元素树
python bgclick.py --title "QQ" --uia-find --uia-name "发送"  # 只查不点
python bgclick.py --title "QQ" --uia-click --uia-name "发送" # 查到就用它点
```

> **纯 ctypes 实现**，没用 `comtypes` / `uiautomation` 包 —— 依然是零第三方依赖。

### 三条要记住的

| 事实 | 说明 |
|---|---|
| **找到元素 ≠ 程序响应了点击** | 和 `--method post` 一回事：投递成功只代表消息进了队列。做不可逆动作前该截图还是得截。 |
| **超限返回的是部分结果，不是报错** | 元素超过 `--limit`（默认 500）时置 `truncated=true` 并照常返回。UIA 碰上几千行的列表会把目标程序卡住，这个上限必须留着。 |
| **默认只读** | `uia` / `--uia-find` 都是纯查询。只有 `--uia-click` 才真的点。 |

### 已知限制（实测）

* **Electron 程序（QQ NT / VS Code / Discord…）树很浅**：可能只有一堆嵌套 `pane`，
  正文和按钮拿不到名字。**但 QQ 实测能拿到**：输入框是 `Edit`、发送按钮是 `Button`，
  所以「Electron 一定不行」是错的 —— 先 `uia` 看一眼再下结论。
* **树大时查询会慢**：QQ 完整树约 365 个节点、单次查询 3~4 秒。加 `--type` / `--name`
  缩小范围**不会更快**（过滤发生在遍历之后），想快只能调小 `--max-depth`。
* **`--limit` 小的时候会先拿到容器**：`pane` 占满预算，具名控件被挤出结果。
  遇到「只有 pane」就调大 `--limit`，或者直接按名字查。

---

## 四、HTTP API

基址 `http://127.0.0.1:8765`。除 `/health` 外都需要
`Authorization: Bearer <token>`（token 在状态目录 `.bgclick/token.txt`）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 探活，无需 token。返回版本、权限、完整性级别、PID |
| GET | `/windows` | 列出所有可见窗口（含 `postable` 字段） |
| GET | `/shutdown` | 停止服务（⚠️ 托盘模式下只停 HTTP、不停进程） |
| POST | `/window` | 单个窗口详情 |
| POST | `/probe` | 探测某窗口能否接收消息（含双方完整性级别） |
| POST | `/click` | 点击（left / right / middle） |
| POST | `/key` | **键盘**：`chord` / `chords` / `keys`(+`hold_seconds`) / `text` |
| POST | `/mouse` | **鼠标**：`dx`/`dy`、`to`、`path`、`pattern`、`scroll`、`button`(拖拽) |
| POST | `/uia` | **元素查询**：按 name / control_type / automation_id / class_name 找元素，拿坐标（v1.5.0） |
| POST | `/screenshot` | 截图 |
| POST | `/text` | 向窗口输入文本（逐字符 `WM_CHAR`） |

**定位窗口的公共字段**（除 `/windows`、`/health`、`/shutdown` 外通用）：

```json
{ "title": "记事本", "exact": false, "regex": false,
  "process": "notepad.exe", "hwnd": "0x00123456", "index": 0 }
```

**`/click`**

```json
{ "title": "记事本", "center": true,
  "count": 3, "interval": 1.0, "button": "left",
  "method": "post", "jitter": 0, "activate": false }
```

`pos` / `center` / `screen_pos` 三选一。`method` 取 `post` / `send` / `hardware`。
返回 `clicked`、`failed` 和最近 10 条明细。

**`/key`**

```json
{ "title": "记事本", "chord": "ctrl+shift+s" }
{ "title": "记事本", "chords": ["ctrl+a", "ctrl+c"], "repeat": 2 }
{ "title": "记事本", "keys": ["ctrl","shift","a"], "hold": true, "hold_seconds": 1.5 }
{ "title": "记事本", "text": "Hello!", "mode": "auto" }
{ "title": "记事本", "chord": "ctrl+s", "method": "sendinput", "scancode": true }
```

`chord` / `chords` / `keys` / `text` **四选一**。返回 `key` 对象，含
`sent`、`failed`、`mode`、`elapsed`、`events`（最近 40 条）。

* `method` 取 `post` / `send` / `sendinput`。`sendinput` 走系统输入队列，
  **默认只发扫描码**（`"scancode": false` 可改用虚拟键码），并且会先把目标窗口
  调到前台（真键盘是全局的）。
* `text` 的 `mode` 取 `auto`（默认，每字符只走一条通道）/ `keys` / `both`。

**`/mouse`**

```json
{ "title": "记事本", "dx": 0, "dy": -500 }
{ "title": "记事本", "to": [400, 300], "steps": 30, "ease": true }
{ "title": "记事本", "path": [[100,100],[300,300]] }
{ "title": "记事本", "pattern": "circle", "distance": 200 }
{ "title": "记事本", "from": [200,400], "dy": -300, "button": "left" }
{ "title": "记事本", "scroll": -5, "axis": "vertical" }
```

滑动写法**五选一**：`dx`/`dy`、`to`、`path`、`pattern`、`scroll`。
给了 `button`（或 `drag`）就是按住拖动。返回 `mouse` 对象，含
`mode`、`from`、`to`、`steps`、`sent`、`failed`。

* `method` 取 `post` / `send` / `hardware` / `sendinput`。后两者会真实移动光标。
* 坐标是**客户区坐标**；滚轮的 `lParam` 按 Windows 规定用屏幕坐标（库内部换算）。
* `batch`（默认 true）只对 `sendinput` 有效：整批原子提交。

**`/uia`**（元素查询，v1.5.0 新增）

```json
{ "title": "QQ" }
{ "title": "QQ", "name": "发送" }
{ "title": "QQ", "control_type": "Button", "interactive_only": true }
{ "title": "QQ", "automation_id": "okBtn", "name_exact": true }
{ "title": "QQ", "max_depth": 12, "limit": 500 }
```

查找条件 `name` / `control_type` / `automation_id` / `class_name` 都可选（至少给一个
才有筛选意义），配 `name_exact` / `interactive_only` / `max_depth`（默认 12）/
`limit`（默认 500）。**不给任何条件就是遍历整棵树**（等价于 `--uia-walk`）。
GET 和 POST 都行，方便快速试。

返回 `uia` 对象，含 `count`、`truncated`、`limit`、`visited`、`max_depth`、
`elapsed`、`failed_reason` 和 `elements`。每个元素带：

| 字段 | 说明 |
|---|---|
| `rect` | `[left, top, right, bottom]`，**屏幕坐标** |
| `center` | 屏幕坐标中心点 |
| **`client_center`** | **客户区坐标**中心点 —— 直接喂给 `/click` 的 `pos` |
| `name` / `control_type` / `automation_id` / `class_name` | 元素标识 |
| `is_interactive` | 是否疑似可交互 |

> ★ `limit` 超了返回**部分结果**并置 `truncated=true`，不是报错 ——
> UIA 碰上几千行的列表会把目标程序卡住，上限必须留着。

**`/screenshot`**

```json
{ "title": "记事本", "path": "note.png", "format": "png",
  "method": "auto", "client_only": false, "inline": false }
```

返回 `path`、`width`、`height`、`bytes`、`capture_method`。
`inline: true` 会额外返回 `base64`。

**`/text`**

```json
{ "title": "记事本", "text": "hello", "enter": true }
```

返回统一形如：

```json
{ "ok": true,  "screenshot": { "...": "..." } }
{ "ok": false, "error": "原因", "code": 2 }
```

---

## 五、安全设计（重要）

这个服务跑在**管理员权限**下、还能模拟鼠标键盘，所以它本质上是一块**提权面**。
下面每条限制都是刻意的：

| 限制 | 防的是什么 |
|---|---|
| **只绑 `127.0.0.1`** | 局域网 / 外网根本连不上 |
| **强制 Bearer token**（随机 32 字节，`token.txt`，权限仅当前用户） | 本机其他用户或程序顺手驱动它 |
| **校验 `Host` 头**（只收 localhost / 127.0.0.1 / ::1） | DNS rebinding —— 否则恶意网页能把你的浏览器当跳板 |
| **截图路径限制在允许目录内** | 被用来覆盖任意文件（如 `C:\Windows\...`） |
| **单次点击次数上限**（默认 10000） | 一个错误请求把目标程序点爆 |
| **滑动/键盘有步数与长度上限**（`steps` ≤ 5000、`repeat` ≤ 1000、`text` ≤ 4096 字符） | 一次请求刷爆目标 |
| **`hardware` / `sendinput` 必须显式指定**，不会自动降级 | 避免突然抢走你的鼠标 |
| **不提供任意命令执行** | 只有点击/键盘/滑动/截图这些动作，没有 shell |

放开的办法是命令行参数（`--max-clicks`、`--extra-shot-dir`），**不要去改代码里的检查**。

---

## 六、权限（UIPI）：点不动怎么办

Windows 的 UIPI 规则：**只有完整性级别(IL) >= 目标 IL 的进程才能给目标窗口发消息**，
低发高一律丢弃，`PostMessageW` 返回 `FALSE` + 错误码 5。

| 进程类型 | 完整性级别 |
|---|---|
| 普通程序 | Medium |
| 管理员提权后 | High |
| **受限 / 沙箱终端** | **Low ← 陷阱** |

**实测踩过的坑**：

1. **UIPI 只放行少数「无害」消息**（`WM_NULL`、`WM_MOVE`）。
   所以拿 `WM_NULL` 当「能不能点」的探针**永远是假阳性** ——
   本工具用 `WM_MOUSEMOVE` 探针，和真实点击同源。
2. **鼠标类消息一律走 UIPI 过滤**（`WM_MOUSEMOVE`、`WM_LBUTTONDOWN`、`WM_USER`、`WM_APP` 实测全部被拒）。
3. **如果本进程是 Low（受限/沙箱终端就长这样），它发不进任何普通程序** ——
   这跟目标是谁无关，**提权也救不了**，得换普通 PowerShell / cmd。

排查顺序：

```bash
python bgclick.py --scan      # 看哪些能点，第 1 行会显示自己的完整性级别
python bgclick.py --doctor --title "xxx" --pos 1,1   # 逐层探测卡在哪
```

程序**自己会申请管理员权限**：撞到 UIPI 时会自动弹 UAC 并在新窗口继续。
但它不会乱弹 —— 已提权 / 目标 IL 不比自己高 / 自己在 Low 沙箱里，这三种情况都不弹。

---

## 七、打包成 exe

```bash
pip install pyinstaller
pyinstaller bgserver.spec --noconfirm
```

产物是 **onedir**：`dist/bgserver/bgserver.exe` + `dist/bgserver/_internal/`。
★ `_internal` 必须跟 exe 待在一起，别只搬 exe。

不想自己打包的话，[Releases](https://github.com/christomas11/bgclick-win32/releases)
里有打好的 zip，解压即用。

> ⚠️ 附件里的 exe 是 **v1.4.0** 构建的（不含 UIA）。要用 UIA 功能请自己打包，
> 或直接跑源码 `python bgserver.py`。

---

## 八、已知限制

* **合成消息天生无效的目标**：DirectX / Vulkan 独占全屏游戏、用 Raw Input 自己读鼠标的软件、
  Chrome / Electron 的部分区域、无边框全屏窗口。
  这些只能上 `--method hardware` / `sendinput`（真输入，会动光标）。
* **鼠标「移动」比「点击」更容易失效**：位置是全局状态，程序会查真实光标
  （`GetCursorPos`）；点击是事件，程序通常照单全收。
  所以「点击能行、滑动不行」是正常现象，不是 bug。
* **真输入（`hardware` / `sendinput`）会占用你的真实光标**，属于前台行为：
  - 滚轮发给**光标底下那个窗口**，所以会先把光标挪到目标位置；
  - 目标窗口必须可见、没被完全遮住，否则操作的是上层窗口；
  - 默认结束后把光标放回原处，`--no-restore-cursor` 可关；
  - `--count 0` 无限循环时不会恢复光标。
* **`sendinput` 注入仍可被识别**：`RAWINPUTHEADER.hDevice == NULL`，
  低级钩子有 `LLMHF_INJECTED` / `LLKHF_INJECTED`。要绕过反作弊只有驱动级
  虚拟 HID 方案，本工具不做。
* **`PrintWindow` 不是万能的**。实测在部分环境会被拒（本机资源管理器就走了 `bitblt` 回退），
  此时抓的是屏幕像素 —— **窗口被遮挡的部分会是黑的**。
  返回里的 `capture_method` 会告诉你实际用了哪条路（`printwindow-fullcontent` /
  `printwindow` / `bitblt`）。
* **最小化窗口**截出来可能是黑图，建议先还原。
* **`PostMessageW` 返回成功只代表操作系统收下了消息**，不代表目标程序一定会响应。
  这两件事必须分开看。
* **托盘模式下 `bgclient.py shutdown` 退不干净**：它只停 HTTP 线程，托盘消息循环还阻塞着，
  但返回 `{"ok": true}`。而服务是管理员 / High 完整性级别，普通终端 `taskkill` 也会
  Access denied。请从任务栏 `^` 折叠区右键托盘图标选「退出」，
  或用管理员 `taskkill /PID <pid> /F`。
  起服务时带 `--no-tray` 可以避开这个坑。

---

## 九、项目结构

```
bgclient.py            零依赖 CLI 客户端（兼容壳）
bgclick.py             核心库 + 独立 CLI（兼容壳）
bgserver.py            常驻服务源码 / 打包入口（兼容壳）
bgtray.py              托盘图标模块（兼容壳）

bgkit/                 ★ 真正的实现
  bgclick/             Win32 底层：win32 / mouse / keyinput / keys /
                       sendinput / clicking / screen / uia / geometry /
                       targeting / wininfo / elevation / integrity / messaging
  bgclient/            HTTP 客户端、自动拉起、状态目录
  bgserver/            HTTP API、安全（token / Host 校验 / 路径白名单）、
                       单实例锁、提权、日志、托盘
  bgtray/              ctypes 托盘图标

bgserver.spec          PyInstaller 打包配置
SKILL.md               给 AI agent 用的操作手册（skill 形态）
docs/常用指令.md        命令速查
```

模块拆分原则：**兼容壳保名字，实现按职责分**。`import bgclick as bc`、`bc.user32`、
`python bgclick.py --list` 这些老用法在拆包后一律照旧。

---

## 十、环境与版本历史

* Windows（依赖 user32 / gdi32 / kernel32 / advapi32 / shell32）
* Python 3.8+（实测 3.12.4）
* **零第三方依赖** —— 只用标准库，PNG 编码也是自己写的（zlib）
* 服务版本 **v1.5.0**（`python bgclient.py health` 可确认）

| 版本 | 新增 |
|---|---|
| 1.0.1 | 点击 / 截图 / 文本 / 托盘 / 单实例锁 |
| 1.1.0 | `/key` 键盘：组合键、多键同按、逐字符 |
| 1.2.0 | `/mouse` 滑动 / 拖拽 / 滚轮；`/key` 的 `--type` 字符不再翻倍 |
| 1.3.0 | `--method hardware` 真输入（`SetCursorPos` + `mouse_event`） |
| 1.4.0 | `--method sendinput`（系统输入队列、整批原子提交）；键盘扫描码路径 |
| **1.5.0** | **UIA 元素查询**（`/uia`、`uia` 子命令、`--uia-*`）；`/health` 增加 `uia` 字段 |

---

## 十一、免责声明 / 使用边界

这是**本机自动化工具**，跑在你自己的电脑上、操作你自己开的窗口。请遵守：

* 只在你**有权操作**的程序和数据上使用；
* **不要**拿它做游戏反作弊规避、刷量、灌水、自动化薅羊毛这类事；
* 服务跑在管理员权限下且没有沙箱，**别把它暴露到 127.0.0.1 之外**，
  也别把 token 给别人；
* 真输入模式会抢占你的鼠标键盘，跑之前想清楚它在点什么。

软件按 MIT 协议「原样」提供，作者不对使用后果负责。

---

## License

[MIT](LICENSE) © 2026 Christomas
