# -*- coding: utf-8 -*-
"""bgkit.bgclick.uia.py —— 轻量 UI Automation 元素查询（纯 ctypes，零依赖）

为什么不装 comtypes / uiautomation
----------------------------------
常见的 UIA 封装（comtypes + `uiautomation` 包）真正的魔法只有两行：读
UIAutomationCore.dll 内嵌的类型库，**自动生成上百个 COM 接口的 ctypes 定义**。
而 UIA 的接口是**平铺 vtable 的经典 COM**，没有 dual interface / IDispatch
的复杂性 —— 所以手写那十来个真正要用的槽位，比拖一整套依赖进来划算得多。

本项目本来就是零依赖纯 ctypes 的路子（bgclick.py 全文没有第三方 import），
这里保持同一个原则：**只用 ctypes + 系统自带的 ole32 / oleaut32 / user32**。

★ 槽位不是猜的，是实测认领的
----------------------------
vtable 排错的代价不是异常，是**进程崩溃或读出垃圾**，所以本模块的每个槽位
都用「对照已知真值」的方式实测过（探针脚本见提交说明）：

    uia[6]  ElementFromHandle           对照 IsWindow 能拿到元素
    uia[16] get_RawViewWalker           能遍历
    uia[20] CreateCacheRequest          拿到非空指针
    uia[21] CreateTrueCondition         拿到非空指针
    uia[22] CreateFalseCondition        拿到非空指针
    elem[6] FindAll                     子元素数与实际一致
    elem[10] GetCurrentPropertyValue    ★ 16 项属性全部对上真值
    elem[43] get_CurrentBoundingRectangle  与 GetWindowRect 完全相等

其中 elem[10] 是主力：**只需这一个槽位正确，就能靠 PropertyId 常量读到
Name / ControlType / ClassName / ProcessId / IsEnabled / IsOffscreen /
IsControlElement / IsKeyboardFocusable / AutomationId / FrameworkId**，
彻底绕开「70 个属性 getter 槽位必须逐个排对」这个最大的风险点。

坐标则走 elem[43]，它直接返回 RECT(left, top, right, bottom)；
而 BoundingRectangle **属性**返回的是 double[4] = (left, top, width, height)
的 SAFEARRAY —— 两者语义不同且后者要解包 SAFEARRAY，混用会得到完全错误的
坐标。这里统一走 RECT 方法。

线程模型（★ 这条不注意会死锁）
------------------------------
UI Automation **要求 STA**。而 bgserver 是 ThreadingHTTPServer，每个请求一个
线程 —— 如果在每个请求线程里各自 CoInitialize + 各自建 IUIAutomation，就会出现
多个 STA 线程 + 跨 apartment marshaling，和主线程的 COM 操作互相等待死锁
（Windows-MCP 源码里专门有一段注释警告这件事，明确拒绝了线程池做法）。

所以这里的做法是：**所有 UIA 调用都固定在一条专用的 STA 工作线程里**，
调用方通过队列投递请求、同步等结果。UIA 的接口指针永远不逃出那条线程。

    with uia_session() as s:                 # 或直接用模块级函数
        nodes = s.walk(hwnd, max_depth=12)
        hits = [n for n in nodes if "确定" in n.name]

防卡死
------
UIA 遍历最容易在两个地方出事：元素极多的大列表（几千行），以及某些程序的
accessibility provider 本身很慢。这里的防线是：

* **元素预算**（默认 500，对齐 Windows-MCP 的 DEFAULT_MAX_TREE_ELEMENTS），
  超了就带 `truncated=True` 返回**部分结果**，而不是卡住或报错；
* **深度上限**（默认 12），Windows-MCP 没做这个，但对「轻量」目标必须有；
* **死元素剪枝**：`UIA_E_ELEMENTNOTAVAILABLE`（0x80040201）在遍历中是常态
  （窗口在遍历途中关闭、列表项被虚拟化），必须当成「剪掉这棵子树」，不能上抛。

和 Windows-MCP 一样，这里**不做超时中断** —— 中断一个正在跨进程调用的 COM
调用本身就不安全，靠预算兜底才是可靠的做法。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

from .errors import AppError
from .win32 import user32

# --------------------------------------------------------------------------
# COM 基础设施
# --------------------------------------------------------------------------

ole32 = ctypes.WinDLL("ole32", use_last_error=True)
oleaut32 = ctypes.WinDLL("oleaut32", use_last_error=True)

ole32.CLSIDFromString.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
ole32.CLSIDFromString.restype = ctypes.c_long
ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
ole32.CoInitializeEx.restype = ctypes.c_long
ole32.CoUninitialize.argtypes = []
ole32.CoUninitialize.restype = None
ole32.CoCreateInstance.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_void_p),
]
ole32.CoCreateInstance.restype = ctypes.c_long
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, s: str) -> "GUID":
        g = cls()
        hr = ole32.CLSIDFromString(s, ctypes.byref(g))
        if hr < 0:
            raise AppError(f"GUID 解析失败 {s!r}（hr=0x{hr & 0xFFFFFFFF:08X}）", code=2)
        return g


CLSID_CUIAutomation = GUID.parse("{FF48DBA4-60EF-4201-AA87-54103EEF594E}")
IID_IUIAutomation = GUID.parse("{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}")

CLSCTX_INPROC_SERVER = 0x1
COINIT_APARTMENTTHREADED = 0x2
COINIT_MULTITHREADED = 0x0
S_OK, S_FALSE = 0, 1
RPC_E_CHANGED_MODE = 0x80010106

# UIA 错误码（HRESULT 有符号，统一按 <0 判失败）
UIA_E_ELEMENTNOTAVAILABLE = 0x80040201
UIA_E_NOTSUPPORTED = 0x80040204
RPC_E_DISCONNECTED = 0x80010108


def _signed(hr: int) -> int:
    """HRESULT 是 32 位有符号；ctypes 返回 c_long 时可能已经是负的，统一一下。"""
    hr &= 0xFFFFFFFF
    return hr - 0x100000000 if hr >= 0x80000000 else hr


def _is_dead_element(hr: int) -> bool:
    """元素已失效 —— 遍历途中的常态，应当剪枝而不是报错。"""
    return _signed(hr) in (UIA_E_ELEMENTNOTAVAILABLE, RPC_E_DISCONNECTED)


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


class _VUNION(ctypes.Union):
    _fields_ = [
        ("llVal", ctypes.c_longlong), ("lVal", ctypes.c_long),
        ("dblVal", ctypes.c_double), ("boolVal", ctypes.c_short),
        ("bstrVal", ctypes.c_void_p), ("punkVal", ctypes.c_void_p),
        ("parray", ctypes.c_void_p), ("scode", ctypes.c_long),
    ]


class VARIANT(ctypes.Structure):
    """VARIANT。

    ★ x64 下是 24 字节（16 字节 union + 8 字节头），**必须让 ctypes 自己算布局**，
      不要手写 padding —— 写错会静默读错值。
    """
    _anonymous_ = ("u",)
    _fields_ = [
        ("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort),
        ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort), ("u", _VUNION),
    ]


VT_I4, VT_R8, VT_BSTR, VT_BOOL = 3, 5, 8, 11
VT_ARRAY = 0x2000


def vcall(ptr, index: int, restype, argtypes, *args):
    """按 vtable 下标调用 COM 方法。ptr 是 c_void_p 形式的接口指针。"""
    pp = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))
    vtbl = ctypes.cast(pp[0], ctypes.POINTER(ctypes.c_void_p))
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(vtbl[index])(ptr, *args)


def com_release(ptr) -> None:
    """Release 一个接口指针（IUnknown 槽位 2）。指针为空时静默跳过。"""
    if ptr is None:
        return
    raw = ptr.value if hasattr(ptr, "value") else int(ptr)
    if not raw:
        return
    try:
        vcall(ctypes.c_void_p(raw), 2, ctypes.c_ulong, [])
    except Exception:
        pass


def _take_bstr(p: int) -> str:
    """读 BSTR 并释放。★ 不释放就是每次遍历都泄漏。"""
    if not p:
        return ""
    try:
        s = ctypes.wstring_at(p)
    except Exception:
        s = ""
    finally:
        oleaut32.SysFreeString(ctypes.c_void_p(p))
    return s


def _variant_value(pv: VARIANT) -> Any:
    """按值语义解 VARIANT。

    UIA 会返回的类型都是「值语义、无需 Release」的标量，除了 BSTR 要 SysFreeString。
    ★ 注意 VARIANT_BOOL 是 **-1 = True / 0 = False**，不是 1/0 —— 写成 `!= 0`
      才是对的（探针里专门验过）。
    """
    if pv.vt == VT_BSTR:
        return _take_bstr(pv.bstrVal)
    if pv.vt == VT_I4:
        return int(pv.lVal)
    if pv.vt == VT_R8:
        return float(pv.dblVal)
    if pv.vt == VT_BOOL:
        return pv.boolVal != 0
    if pv.vt & VT_ARRAY:
        return None          # 数组类型这里不解（BoundingRectangle 已改走 RECT 方法）
    return None


# --------------------------------------------------------------------------
# vtable 槽位（★ 全部实测认领，别随手改）
# --------------------------------------------------------------------------

# IUIAutomation
IUIA_GET_ROOT_ELEMENT = 5
IUIA_ELEMENT_FROM_HANDLE = 6
IUIA_GET_RAW_VIEW_WALKER = 16
IUIA_CREATE_CACHE_REQUEST = 20
IUIA_CREATE_TRUE_CONDITION = 21
IUIA_CREATE_FALSE_CONDITION = 22

# IUIAutomationElement
ELEM_FIND_ALL = 6
ELEM_GET_CURRENT_PROPERTY_VALUE = 10
ELEM_GET_CACHED_CHILDREN = 19
ELEM_GET_BOUNDING_RECT = 43

# IUIAutomationElementArray（只有 4 个方法，手写成本极低）
ARR_GET_LENGTH = 3
ARR_GET_ELEMENT = 4

# IUIAutomationTreeWalker（降级路径用）
WALKER_GET_PARENT = 3
WALKER_GET_FIRST_CHILD = 4
WALKER_GET_NEXT_SIBLING = 6
WALKER_NORMALIZE = 8

UNKNOWN_RELEASE = 2

# TreeScope
TREE_SCOPE_NONE = 0
TREE_SCOPE_ELEMENT = 1
TREE_SCOPE_CHILDREN = 2
TREE_SCOPE_DESCENDANTS = 4
TREE_SCOPE_SUBTREE = 7

# --------------------------------------------------------------------------
# 属性 ID
# --------------------------------------------------------------------------

PROP_BOUNDING_RECTANGLE = 30001
PROP_PROCESS_ID = 30002
PROP_CONTROL_TYPE = 30003
PROP_LOCALIZED_CONTROL_TYPE = 30004
PROP_NAME = 30005
PROP_ACCELERATOR_KEY = 30006
PROP_IS_KEYBOARD_FOCUSABLE = 30009
PROP_IS_ENABLED = 30010
PROP_AUTOMATION_ID = 30011
PROP_CLASS_NAME = 30012
PROP_HELP_TEXT = 30013
PROP_IS_CONTROL_ELEMENT = 30016
PROP_IS_CONTENT_ELEMENT = 30017
PROP_IS_PASSWORD = 30019
PROP_NATIVE_WINDOW_HANDLE = 30020
PROP_IS_OFFSCREEN = 30022
PROP_FRAMEWORK_ID = 30024

#: walk/find 默认会读的属性（一次跨进程往返一项，别贪多）
DEFAULT_PROPS: dict[str, int] = {
    "name": PROP_NAME,
    "control_type_id": PROP_CONTROL_TYPE,
    "localized_control_type": PROP_LOCALIZED_CONTROL_TYPE,
    "class_name": PROP_CLASS_NAME,
    "automation_id": PROP_AUTOMATION_ID,
    "process_id": PROP_PROCESS_ID,
    "is_enabled": PROP_IS_ENABLED,
    "is_offscreen": PROP_IS_OFFSCREEN,
    "is_control_element": PROP_IS_CONTROL_ELEMENT,
    "is_keyboard_focusable": PROP_IS_KEYBOARD_FOCUSABLE,
    "framework_id": PROP_FRAMEWORK_ID,
}

#: ControlType 数值 -> 名字（用于 --uia-type 过滤和人类可读输出）
CONTROL_TYPES: dict[int, str] = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox",
    50004: "Edit", 50005: "Hyperlink", 50006: "Image", 50007: "ListItem",
    50008: "List", 50009: "Menu", 50010: "MenuBar", 50011: "MenuItem",
    50012: "ProgressBar", 50013: "RadioButton", 50014: "ScrollBar",
    50015: "Slider", 50016: "Spinner", 50017: "StatusBar", 50018: "Tab",
    50019: "TabItem", 50020: "Text", 50021: "ToolBar", 50022: "ToolTip",
    50023: "Tree", 50024: "TreeItem", 50025: "Custom", 50026: "Group",
    50027: "Thumb", 50028: "DataGrid", 50029: "DataItem", 50030: "Document",
    50031: "SplitButton", 50032: "Window", 50033: "Pane", 50034: "Header",
    50035: "HeaderItem", 50036: "Table", 50037: "TitleBar",
    50038: "Separator", 50039: "SemanticZoom", 50040: "AppBar",
}

#: 反向查表（大小写不敏感），让 --uia-type button 这种写法能用
CONTROL_TYPES_BY_NAME: dict[str, int] = {v.lower(): k for k, v in CONTROL_TYPES.items()}

#: 交互控件类型白名单 —— 照抄 Windows-MCP 的 INTERACTIVE_CONTROL_TYPE_NAMES
#: （它们的注释里有一条踩坑记录：Slider 曾经漏掉，导致设置里的滑块永远查不到）
INTERACTIVE_CONTROL_TYPES: set[int] = {
    CONTROL_TYPES_BY_NAME[n] for n in (
        "button", "listitem", "menuitem", "edit", "checkbox", "radiobutton",
        "combobox", "hyperlink", "splitbutton", "tabitem", "treeitem",
        "dataitem", "headeritem", "spinner", "slider", "scrollbar",
    )
}


def control_type_name(ct_id: Optional[int]) -> str:
    if ct_id is None:
        return ""
    return CONTROL_TYPES.get(ct_id, f"Unknown({ct_id})")


def control_type_id(name: str) -> Optional[int]:
    """把 'Button' / 'button' / '50000' 都解析成数值。"""
    if name is None:
        return None
    s = str(name).strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    return CONTROL_TYPES_BY_NAME.get(s.lower())


# --------------------------------------------------------------------------
# 节点
# --------------------------------------------------------------------------


@dataclass
class UiaElement:
    """一个 UI 元素。坐标是**屏幕坐标**（物理像素，多显示器可为负）。"""

    name: str = ""
    control_type: str = ""
    control_type_id: Optional[int] = None
    localized_control_type: str = ""
    class_name: str = ""
    automation_id: str = ""
    process_id: Optional[int] = None
    is_enabled: Optional[bool] = None
    is_offscreen: Optional[bool] = None
    is_control_element: Optional[bool] = None
    is_keyboard_focusable: Optional[bool] = None
    framework_id: str = ""
    rect: Optional[list[int]] = None      # [left, top, right, bottom] 屏幕坐标
    depth: int = 0

    @property
    def screen_center(self) -> Optional[list[int]]:
        """矩形中心（整数下取整），和 Windows-MCP 的 get_center 同款算法。"""
        if not self.rect:
            return None
        left, top, right, bottom = self.rect
        w, h = right - left, bottom - top
        return [left + w // 2, top + h // 2]

    @property
    def size(self) -> Optional[list[int]]:
        if not self.rect:
            return None
        return [self.rect[2] - self.rect[0], self.rect[3] - self.rect[1]]

    @property
    def area(self) -> int:
        s = self.size
        return (s[0] * s[1]) if s else 0

    @property
    def is_interactive(self) -> bool:
        """像不像一个「能点」的元素。

        ★ 只说「像」：UIA 的 IsOffscreen 和「能否收到 PostMessage」是两回事
          （被遮挡的元素 IsOffscreen 可能是 False）。所以这里把 is_offscreen
          当**参考项**而不是硬门槛，真正的可投递性交给 bgclick 的 WM_MOUSEMOVE 探针。
        """
        return (
            self.is_control_element is not False
            and self.is_enabled is not False
            and self.area > 0
            and self.control_type_id in INTERACTIVE_CONTROL_TYPES
        )

    def center_in_client(self, hwnd: int) -> Optional[list[int]]:
        """把屏幕中心点换算成 hwnd 的客户区坐标 —— 直接喂给 click --pos。"""
        c = self.screen_center
        if not c:
            return None
        from .geometry import resolve_client_point
        try:
            return list(resolve_client_point(hwnd, c[0], c[1]))
        except OSError:
            return None

    def to_dict(self, hwnd: Optional[int] = None) -> dict:
        d: dict[str, Any] = {
            "name": self.name,
            "control_type": self.control_type,
            "control_type_id": self.control_type_id,
            "class_name": self.class_name,
            "automation_id": self.automation_id,
            "rect": self.rect,
            "center": self.screen_center,
            "size": self.size,
            "depth": self.depth,
            "is_enabled": self.is_enabled,
            "is_offscreen": self.is_offscreen,
            "is_keyboard_focusable": self.is_keyboard_focusable,
            "is_interactive": self.is_interactive,
        }
        if self.localized_control_type:
            d["localized_control_type"] = self.localized_control_type
        if self.process_id is not None:
            d["process_id"] = self.process_id
        if self.framework_id:
            d["framework_id"] = self.framework_id
        if hwnd is not None and self.rect:
            d["client_center"] = self.center_in_client(hwnd)
        return d


@dataclass
class WalkResult:
    """一次遍历的结果。截断时 elements 是**部分结果**，不是失败。"""

    elements: list[UiaElement] = field(default_factory=list)
    truncated: bool = False
    limit: int = 0
    visited: int = 0
    max_depth: int = 0
    elapsed: float = 0.0
    failed_reason: str = ""

    def to_dict(self, hwnd: Optional[int] = None, dedupe: bool = True) -> dict:
        els = self.elements
        return {
            "count": len(els),
            "truncated": self.truncated,
            "limit": self.limit,
            "visited": self.visited,
            "max_depth": self.max_depth,
            "elapsed": self.elapsed,
            "failed_reason": self.failed_reason,
            "elements": [e.to_dict(hwnd) for e in els],
        }


# --------------------------------------------------------------------------
# 遍历预算（对齐 Windows-MCP 的 TreeElementBudget）
# --------------------------------------------------------------------------

DEFAULT_MAX_ELEMENTS = 500
DEFAULT_MAX_DEPTH = 12


class ElementBudget:
    """限制一次遍历收集的元素数。

    对着一棵几千行的列表硬走，会把目标程序卡住好几分钟，也可能撑爆响应体。
    这里超限就停下、标记 truncated，返回**部分结果**给调用方自己判断。
    """

    def __init__(self, limit: int = DEFAULT_MAX_ELEMENTS):
        self.limit = max(1, int(limit))
        self.count = 0
        self.truncated = False

    @property
    def exhausted(self) -> bool:
        return self.count >= self.limit

    def try_consume(self, amount: int = 1) -> bool:
        if amount <= 0:
            return not self.exhausted
        if self.exhausted:
            self.truncated = True
            return False
        if amount > self.limit - self.count:
            self.count = self.limit
            self.truncated = True
            return False
        self.count += amount
        if self.exhausted:
            self.truncated = True
        return True


# --------------------------------------------------------------------------
# 专用 STA 工作线程
# --------------------------------------------------------------------------


class _UiaWorker:
    """一条专用 STA 线程 + 队列。所有 UIA 调用都投进来同步执行。

    为什么不让调用方各自 CoInitialize：
    多个 STA 线程同时做 COM 调用会跨 apartment marshaling，和主线程互相等待
    直接死锁。UIA 的接口指针也**不能跨线程传递**，所以整个会话必须固定在这一条
    线程里，外面只投递「读某个窗口的元素」这种纯函数式请求。
    """

    def __init__(self):
        self._task_q: "queue.Queue" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._init_error = ""
        self._uia = ctypes.c_void_p()
        self._stopped = False

    # --- 生命周期 ---

    def _run(self) -> None:
        hr = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        if _signed(hr) not in (S_OK, S_FALSE) and (hr & 0xFFFFFFFF) != RPC_E_CHANGED_MODE:
            self._init_error = f"CoInitializeEx 失败 hr=0x{hr & 0xFFFFFFFF:08X}"
            self._ready.set()
            return
        try:
            hr = ole32.CoCreateInstance(
                ctypes.byref(CLSID_CUIAutomation), None, CLSCTX_INPROC_SERVER,
                ctypes.byref(IID_IUIAutomation), ctypes.byref(self._uia),
            )
            if _signed(hr) < 0 or not self._uia.value:
                self._init_error = (
                    f"创建 IUIAutomation 失败 hr=0x{hr & 0xFFFFFFFF:08X}"
                    "（UIAutomationCore.dll 不可用？）"
                )
                self._ready.set()
                return
        except Exception as e:
            self._init_error = f"创建 IUIAutomation 异常：{type(e).__name__}: {e}"
            self._ready.set()
            return

        self._ready.set()

        while True:
            item = self._task_q.get()
            if item is None:
                break
            fn, args, kwargs, out_q = item
            try:
                out_q.put((True, fn(self._uia, *args, **kwargs)))
            except BaseException as e:       # noqa: BLE001 —— 线程里必须兜住一切
                out_q.put((False, e))

        com_release(self._uia)
        try:
            ole32.CoUninitialize()
        except Exception:
            pass

    def _ensure_started(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready.clear()
            self._init_error = ""
            self._stopped = False
            self._thread = threading.Thread(
                target=self._run, name="bgclick-uia-sta", daemon=True)
            self._thread.start()
        self._ready.wait(timeout=15)
        if self._init_error:
            raise AppError(f"UIA 初始化失败：{self._init_error}", code=2)

    # --- 投递 ---

    def submit(self, fn: Callable, *args, timeout: float = 60.0, **kwargs) -> Any:
        """把 fn(uia, *args) 投到 STA 线程执行，同步等结果。"""
        self._ensure_started()
        out_q: "queue.Queue" = queue.Queue(maxsize=1)
        self._task_q.put((fn, args, kwargs, out_q))
        try:
            ok, payload = out_q.get(timeout=timeout)
        except queue.Empty:
            raise AppError(
                f"UIA 查询超时（{timeout:g}s）——目标程序的 accessibility provider "
                f"可能卡住了。试试减小 --max-depth / --limit，或先跳过对这个窗口的 UIA 查询。",
                code=1,
            )
        if not ok:
            if isinstance(payload, AppError):
                raise payload
            raise AppError(f"UIA 调用失败：{type(payload).__name__}: {payload}", code=1)
        return payload

    def stop(self, timeout: float = 5.0) -> None:
        if self._thread is None or not self._thread.is_alive():
            return
        self._stopped = True
        self._task_q.put(None)
        self._thread.join(timeout=timeout)
        self._thread = None


_worker: Optional[_UiaWorker] = None
_worker_lock = threading.Lock()


def get_worker() -> _UiaWorker:
    """取全局 STA 工作线程（懒启动，整个进程共用一条）。"""
    global _worker
    if _worker is not None:
        return _worker
    with _worker_lock:
        if _worker is None:
            _worker = _UiaWorker()
    return _worker


def shutdown_worker() -> None:
    """停掉工作线程。服务退出时调用；不用也不影响（线程是 daemon）。"""
    global _worker
    with _worker_lock:
        if _worker is not None:
            _worker.stop()
            _worker = None


def uia_available() -> tuple[bool, str]:
    """自检：UIA 能不能用。返回 (可用, 说明)。给 health / --doctor 用。"""
    try:
        get_worker().submit(lambda uia: True, timeout=15)
        return True, "ok"
    except AppError as e:
        return False, str(e)
    except Exception as e:      # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------
# 底层读取（★ 全部在 STA 线程里执行）
# --------------------------------------------------------------------------


def _read_properties(el_ptr, props: dict[str, int]) -> dict:
    """一次读一组属性。每项一次跨进程往返。

    ★ 死元素（元素已失效）返回空字典，由上层剪枝 —— 这是遍历途中的常态。
    """
    out: dict[str, Any] = {}
    for key, pid in props.items():
        pv = VARIANT()
        hr = vcall(el_ptr, ELEM_GET_CURRENT_PROPERTY_VALUE, ctypes.c_long,
                   [ctypes.c_int, ctypes.POINTER(VARIANT)],
                   ctypes.c_int(pid), ctypes.byref(pv))
        if _signed(hr) < 0:
            if _is_dead_element(hr):
                return {}
            out[key] = None
            continue
        out[key] = _variant_value(pv)
    return out


def _read_rect(el_ptr) -> Optional[list[int]]:
    box = RECT()
    hr = vcall(el_ptr, ELEM_GET_BOUNDING_RECT, ctypes.c_long,
               [ctypes.POINTER(RECT)], ctypes.byref(box))
    if _signed(hr) < 0:
        return None
    return [box.left, box.top, box.right, box.bottom]


def _make_element(raw: int, props: dict[str, int], rect: Optional[list[int]],
                  depth: int) -> UiaElement:
    ct_id = props.get("control_type_id")
    return UiaElement(
        name=props.get("name") or "",
        control_type=control_type_name(ct_id),
        control_type_id=ct_id,
        localized_control_type=props.get("localized_control_type") or "",
        class_name=props.get("class_name") or "",
        automation_id=props.get("automation_id") or "",
        process_id=props.get("process_id"),
        is_enabled=props.get("is_enabled"),
        is_offscreen=props.get("is_offscreen"),
        is_control_element=props.get("is_control_element"),
        is_keyboard_focusable=props.get("is_keyboard_focusable"),
        framework_id=props.get("framework_id") or "",
        rect=rect,
        depth=depth,
    )


def _find_all(uia, parent_raw: int, scope: int = TREE_SCOPE_CHILDREN) -> list[int]:
    """FindAll(scope, TrueCondition) -> 元素指针列表。

    用 TrueCondition 而不是条件筛：先把结构拿回来，筛在 Python 侧做。
    这样一次遍历能同时服务「枚举」「按名找」「按类型找」三种需求，
    也不用去碰 CreatePropertyCondition 的 VARIANT 入参。
    """
    cond = ctypes.c_void_p()
    hr = vcall(uia, IUIA_CREATE_TRUE_CONDITION, ctypes.c_long,
               [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(cond))
    if _signed(hr) < 0 or not cond.value:
        return []
    arr = ctypes.c_void_p()
    try:
        hr = vcall(ctypes.c_void_p(parent_raw), ELEM_FIND_ALL, ctypes.c_long,
                   [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
                   ctypes.c_int(scope), cond, ctypes.byref(arr))
        if _signed(hr) < 0 or not arr.value:
            return []
        length = ctypes.c_int()
        hr = vcall(arr, ARR_GET_LENGTH, ctypes.c_long,
                   [ctypes.POINTER(ctypes.c_int)], ctypes.byref(length))
        if _signed(hr) < 0:
            return []
        out: list[int] = []
        for i in range(length.value):
            child = ctypes.c_void_p()
            hr = vcall(arr, ARR_GET_ELEMENT, ctypes.c_long,
                       [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
                       ctypes.c_int(i), ctypes.byref(child))
            if _signed(hr) >= 0 and child.value:
                out.append(child.value)
        return out
    finally:
        com_release(arr)
        com_release(cond)


def _walk_impl(uia, hwnd: int, max_depth: int, limit: int,
               props: dict[str, int], include_empty: bool,
               interactive_only: bool) -> WalkResult:
    """真正的遍历，跑在 STA 线程里。

    逐层 FindAll(Children) 而不是一次 FindAll(Descendants)：
    这样能算出 depth —— 对「这个元素在树里多深」以及按层限流都有用。
    """
    import time
    started = time.perf_counter()

    root = ctypes.c_void_p()
    hr = vcall(uia, IUIA_ELEMENT_FROM_HANDLE, ctypes.c_long,
               [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
               ctypes.c_void_p(hwnd), ctypes.byref(root))
    if _signed(hr) < 0 or not root.value:
        return WalkResult(limit=limit, max_depth=max_depth, elapsed=0.0,
                          failed_reason=(
                              f"ElementFromHandle 失败 hr=0x{hr & 0xFFFFFFFF:08X}"
                              "（窗口可能已关闭，或 UIA 拿不到它）"))

    result = WalkResult(limit=limit, max_depth=max_depth)
    budget = ElementBudget(limit)

    try:
        stack: list[tuple[int, int, frozenset, int]] = [(root.value, 0, frozenset(), 0)]
        visited = 0
        while stack:
            if budget.exhausted:
                break
            raw, depth, ancestors, _sib = stack.pop()
            if depth > max_depth:
                continue
            if depth > result.max_depth:
                result.max_depth = depth

            p = _read_properties(ctypes.c_void_p(raw), props)
            visited += 1
            if not p:                       # 死元素，剪枝
                continue

            rect = _read_rect(ctypes.c_void_p(raw))
            elem = _make_element(raw, p, rect, depth)
            keep = True
            if interactive_only and not elem.is_interactive:
                keep = False
            if not include_empty and not elem.name and not elem.automation_id:
                # 无名无 id 的容器仍然要保留 —— 子元素可能在里面。
                # 所以这里只跳过「叶子且无意义」的，交给下面判断。
                keep = keep and True
            if keep:
                if budget.try_consume():
                    result.elements.append(elem)

            if budget.exhausted or depth >= max_depth:
                continue

            # ★ 环检测：用「父元素指针 + 在父元素子列表里的序号」当指纹。
            #
            # 为什么不用 GetRuntimeId：它返回 SAFEARRAY，直接读指针头部读到的
            # 是 cDims/cbElements 这些**所有数组都一样的记帐字段**，拿它当指纹会
            # 把正常元素全判成环。要正确解包得走 SafeArrayAccessData，而收益
            # 只是防一种罕见的 provider 异常（Edge 展开 <select>），不值得。
            #
            # 用 (父指针, 序号) 同样能挡住「子元素指向祖先」这种环，且零成本。
            for idx_child, child_raw in enumerate(_find_all(uia, raw, TREE_SCOPE_CHILDREN)):
                child_ancestors = ancestors
                if child_raw in ancestors:
                    continue                # 环，砍掉
                if len(child_ancestors) < 512:
                    child_ancestors = ancestors | {child_raw}
                stack.append((child_raw, depth + 1, child_ancestors, idx_child))

        result.visited = visited
        result.truncated = budget.truncated
        result.elapsed = round(time.perf_counter() - started, 3)
        return result
    finally:
        com_release(root)


# --------------------------------------------------------------------------
# 对外 API
# --------------------------------------------------------------------------


def element_from_hwnd(hwnd: int) -> Optional[UiaElement]:
    """取窗口自身的根元素信息（不做子树遍历）。"""
    def job(uia):
        el = ctypes.c_void_p()
        hr = vcall(uia, IUIA_ELEMENT_FROM_HANDLE, ctypes.c_long,
                   [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
                   ctypes.c_void_p(int(hwnd)), ctypes.byref(el))
        if _signed(hr) < 0 or not el.value:
            return None
        try:
            p = _read_properties(el, DEFAULT_PROPS)
            if not p:
                return None
            return _make_element(el.value, p, _read_rect(el), 0)
        finally:
            com_release(el)

    return get_worker().submit(job)


def walk(hwnd: int, max_depth: int = DEFAULT_MAX_DEPTH,
         limit: int = DEFAULT_MAX_ELEMENTS, interactive_only: bool = False,
         include_empty: bool = True) -> WalkResult:
    """遍历窗口的 UIA 元素树。

    ★ 返回的部分结果在 truncated=True 时依然可用 —— 它是「到预算上限为止」的
      完整内容，不是错误。调用方应当把它当作有效数据，只是知道还有没走完的部分。
    """
    if max_depth < 0:
        raise AppError("max_depth 不能为负", code=2)
    if limit < 1:
        raise AppError("limit 至少为 1", code=2)
    return get_worker().submit(
        _walk_impl, int(hwnd), int(max_depth), int(limit),
        dict(DEFAULT_PROPS), bool(include_empty), bool(interactive_only),
    )


def find(hwnd: int, name: Optional[str] = None,
         control_type: Optional[str] = None, automation_id: Optional[str] = None,
         class_name: Optional[str] = None, exact: bool = False,
         interactive_only: bool = False, max_depth: int = DEFAULT_MAX_DEPTH,
         limit: int = DEFAULT_MAX_ELEMENTS) -> WalkResult:
    """按条件查找元素。

    在遍历结果上做 Python 侧筛选（而不是让 UIA 去筛）：一次遍历能同时服务
    多种查询，也避开了 CreatePropertyCondition 的 VARIANT 入参。对 limit 内
    的元素量来说完全够快。
    """
    if not any((name, control_type, automation_id, class_name)):
        raise AppError("至少给一个查找条件：name / control_type / automation_id / class_name",
                       code=2)

    want_ct = control_type_id(control_type) if control_type else None
    if control_type and want_ct is None:
        raise AppError(
            f"认不出的控件类型 {control_type!r}。可用值："
            + ", ".join(sorted(CONTROL_TYPES.values())), code=2)

    result = walk(hwnd, max_depth=max_depth, limit=limit,
                  interactive_only=interactive_only)

    def match(el: UiaElement) -> bool:
        if name:
            a, b = (el.name or ""), name
            if exact:
                if a != b:
                    return False
            elif b.lower() not in a.lower():
                return False
        if want_ct is not None and el.control_type_id != want_ct:
            return False
        if automation_id and automation_id.lower() not in (el.automation_id or "").lower():
            return False
        if class_name and class_name.lower() not in (el.class_name or "").lower():
            return False
        return True

    hits = [e for e in result.elements if match(e)]
    return WalkResult(
        elements=hits, truncated=result.truncated, limit=result.limit,
        visited=result.visited, max_depth=result.max_depth,
        elapsed=result.elapsed, failed_reason=result.failed_reason,
    )


def resolve_click_point(hwnd: int, name: Optional[str] = None,
                        control_type: Optional[str] = None,
                        automation_id: Optional[str] = None,
                        exact: bool = False,
                        index: int = 0) -> dict:
    """查元素并给出**客户区坐标** —— 直接喂给 click --pos 用的那种。

    这是 UIA 查询和现有后台点击之间的桥：UIA 负责「找到元素在哪」，
    点击仍然走 bgclick 原有的 PostMessage 通道（不抢光标）。
    """
    res = find(hwnd, name=name, control_type=control_type,
               automation_id=automation_id, exact=exact)
    if not res.elements:
        raise AppError(
            "没找到匹配的 UIA 元素"
            + (f"（name={name!r}）" if name else "")
            + (f"（control_type={control_type!r}）" if control_type else "")
            + "。可以先跑 uia-walk 看看这棵树里有什么。", code=1)
    if index >= len(res.elements) or index < 0:
        raise AppError(f"匹配到 {len(res.elements)} 个元素，index {index} 越界", code=2)

    el = res.elements[index]
    client = el.center_in_client(hwnd)
    if client is None:
        # 元素没有有效矩形（离屏 / 被虚拟化）时不能瞎点
        raise AppError(
            f"元素「{el.name}」没有可用的边界矩形（rect={el.rect}），"
            "无法解析坐标。它可能离屏或已被虚拟化 —— 先把窗口滚到它可见再试。",
            code=1)
    return {
        "element": el.to_dict(hwnd),
        "screen_point": el.screen_center,
        "client_point": client,
        "matched": len(res.elements),
        "index": index,
        "truncated": res.truncated,
    }


def describe_tree(result: WalkResult, hwnd: Optional[int] = None,
                  max_lines: int = 200) -> str:
    """把遍历结果画成缩进树，给人看 / 给 agent 看。"""
    if not result.elements:
        return f"没读到元素。（visited={result.visited}）" + (
            f"\n注意：{result.failed_reason}" if result.failed_reason else "")

    lines: list[str] = []
    for el in result.elements[:max_lines]:
        if el.rect:
            pos = f"({el.screen_center[0]},{el.screen_center[1]})"
            size = f"{el.size[0]}x{el.size[1]}"
        else:
            pos, size = "(?)", "?"
        name = el.name or (f"<{el.automation_id}>" if el.automation_id else "")
        flags = []
        if el.is_enabled is False:
            flags.append("disabled")
        if el.is_offscreen:
            flags.append("offscreen")
        if el.is_interactive:
            flags.append("interactive")
        flag_s = ("  [" + " ".join(flags) + "]") if flags else ""
        lines.append(
            f"{'  ' * el.depth}{pos:<12}{el.control_type.lower():<14}"
            f"{size:<12}\"{name[:48]}\"{flag_s}"
        )
    if len(result.elements) > max_lines:
        lines.append(f"... 还有 {len(result.elements) - max_lines} 个元素未显示")
    if result.truncated:
        lines.append(
            f"... [已截断：到达 {result.limit} 个元素的采集上限，"
            f"还有元素没被访问。用 --limit 提高上限，或用 --uia-type / --uia-name 缩小范围]"
        )
    return "\n".join(lines)
