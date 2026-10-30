# -*- coding: utf-8 -*-
"""纯函数测试 —— 不碰真实窗口，任何 Windows 机器（含 CI）都能跑。

覆盖的是「改错了会静默出错」的那几个点：
坐标打包、PNG 编码、Host 头校验、参数钳位、MCP 工具表。
"""

from __future__ import annotations

import struct

import pytest

from bgkit import __version__, asset_path, script_or_module
from bgkit.bgclick.errors import AppError
from bgkit.bgclick.geometry import make_lparam
from bgkit.bgclick.keys import key_char, parse_chord, parse_pair
from bgkit.bgclick.screen import encode_png_rgb
from bgkit.bgmcp.server import TOOLS
from bgkit.bgserver.security import check_host_header, clamp_int


class TestLParam:
    """make_lparam —— 鼠标消息的坐标打包，错了就是点错地方。"""

    def test_origin(self):
        assert make_lparam(0, 0) == 0

    def test_low_is_x_high_is_y(self):
        assert make_lparam(10, 20) == (20 << 16) | 10

    def test_negative_wraps_to_twos_complement(self):
        # -1 按有符号 16 位是 0xFFFF，这正是 Win32 的约定
        assert make_lparam(-1, -1) == 0xFFFFFFFF

    def test_max_16bit(self):
        assert make_lparam(65535, 65535) == 0xFFFFFFFF

    def test_does_not_overflow_into_neighbour(self):
        # x 超过 16 位时不能污染 y
        assert make_lparam(0x10000, 0) == 0


class TestPngEncoder:
    """手写 PNG 编码器 —— 没人会去肉眼检查图片头，只能靠测试。"""

    def _png(self):
        # 2x2 全红
        return encode_png_rgb(2, 2, bytes([255, 0, 0]) * 4)

    def test_signature(self):
        assert self._png()[:8] == b"\x89PNG\r\n\x1a\n"

    def test_ihdr_dimensions(self):
        png = self._png()
        assert png[12:16] == b"IHDR"
        w, h = struct.unpack(">II", png[16:24])
        assert (w, h) == (2, 2)

    def test_bit_depth_and_color_type(self):
        # 8 位深、颜色类型 2（真彩色）
        png = self._png()
        assert png[24] == 8
        assert png[25] == 2

    def test_has_iend(self):
        assert self._png()[-8:-4] == b"IEND"


class TestHostHeader:
    """防 DNS rebinding：恶意网页把自己的域名解析到 127.0.0.1 时挡下来。"""

    @pytest.mark.parametrize("host", [
        "127.0.0.1:8765", "localhost:8765", "[::1]:8765", "127.0.0.1",
    ])
    def test_local_allowed(self, host):
        assert check_host_header(host) is True

    @pytest.mark.parametrize("host", [
        "evil.com", "evil.com:8765", "127.0.0.1.evil.com",
    ])
    def test_foreign_blocked(self, host):
        assert check_host_header(host) is False


class TestClampInt:
    """clamp_int 不静默截断，越界直接抛错 —— 免得用户传错数量被执行成完全不同的操作。"""

    def test_in_range(self):
        assert clamp_int(5, 0, 10, "x", 1) == 5

    def test_low_raises(self):
        with pytest.raises(AppError):
            clamp_int(-99, 0, 10, "x", 1)

    def test_high_raises(self):
        with pytest.raises(AppError):
            clamp_int(999, 0, 10, "x", 1)

    def test_none_uses_default(self):
        assert clamp_int(None, 0, 10, "x", 7) == 7

    def test_string_digits_accepted(self):
        assert clamp_int("42", 0, 100, "x", 0) == 42

    def test_non_numeric_raises(self):
        with pytest.raises(AppError):
            clamp_int("abc", 0, 100, "x", 0)


class TestKeys:
    def test_parse_pair(self):
        assert parse_pair("100,200") == (100, 200)

    def test_parse_chord_ctrl_s(self):
        vks = parse_chord("ctrl+s")
        assert len(vks) >= 2, f"ctrl+s 应该解析出至少两个键，得到 {vks}"

    def test_letter_produces_lowercase(self):
        # VK_A 在无 Shift 状态下应打出 'a'
        got = key_char(0x41)
        if got is None:
            pytest.skip("当前会话读不到键盘布局（无桌面 session）")
        assert got == "a"

    def test_letter_with_shift_produces_uppercase(self):
        got = key_char(0x41, shift=True)
        if got is None:
            pytest.skip("当前会话读不到键盘布局（无桌面 session）")
        assert got == "A"


class TestPackagingHelpers:
    def test_version_is_string(self):
        assert isinstance(__version__, str) and __version__.count(".") == 2

    def test_asset_path_finds_icon(self):
        # 开发树在根目录，pip 安装版在 bgkit/assets —— 两条路都必须能找到
        assert asset_path("bgclick.ico").endswith("bgclick.ico")

    def test_script_or_module_never_returns_empty(self):
        head = script_or_module("这个文件肯定不存在.py", module="bgkit.bgclick")
        assert head, "找不到脚本时必须退回 -m，不能返回空列表"
        assert head[0].endswith(".py") or head[:2] == ["-m", "bgkit.bgclick"]


class TestMcpToolTable:
    """MCP 工具表是给客户端看的契约，名字撞了或 schema 缺了都会让客户端直接罢工。"""

    def test_tool_count(self):
        assert len(TOOLS) == 10

    def test_names_unique(self):
        names = [t["name"] for t in TOOLS]
        assert len(names) == len(set(names))

    def test_every_tool_has_schema(self):
        for t in TOOLS:
            assert t["inputSchema"]["type"] == "object", t["name"]
            assert t["description"], t["name"]

    def test_target_selector_shared(self):
        # click / screenshot / uia_tree 都必须能按 hwnd 定位
        for name in ("click", "screenshot", "uia_tree", "type_text"):
            tool = next(t for t in TOOLS if t["name"] == name)
            assert "hwnd" in tool["inputSchema"]["properties"], name


class _FakeEl:
    """假的 UIA 元素，只实现 refstore 用到的几个属性。"""

    def __init__(self, name, rect):
        self.name = name
        self.rect = rect
        self.control_type = "Button"
        self.automation_id = "aid-" + name
        self.class_name = "Btn"
        self.is_interactive = True

    @property
    def screen_center(self):
        if not self.rect:
            return None
        left, top, right, bottom = self.rect
        return [left + (right - left) // 2, top + (bottom - top) // 2]

    def center_in_client(self, hwnd=None):
        c = self.screen_center
        return [c[0] - 100, c[1] - 100] if c else None

    def to_dict(self, hwnd=None):
        return {
            "name": self.name, "control_type": self.control_type,
            "automation_id": self.automation_id, "class_name": self.class_name,
            "rect": self.rect, "center": self.screen_center,
            "client_center": self.center_in_client(hwnd),
            "is_interactive": self.is_interactive,
        }


class TestRefStore:
    """@eN 编号体系。编号错位 = 点到别的东西，所以规则必须锁死。"""

    def test_normalize_accepts_three_forms(self):
        from bgkit.bgclick import refstore
        for raw in ("@e3", "e3", "3"):
            assert refstore.normalize(raw) == "@e3"

    def test_normalize_rejects_garbage(self):
        from bgkit.bgclick import refstore
        for raw in ("hello", "@x1", "", "e"):
            with pytest.raises(refstore.RefError):
                refstore.normalize(raw)

    def test_assign_skips_elements_without_rect(self, tmp_path, monkeypatch):
        from bgkit.bgclick import refstore
        monkeypatch.setenv("BGCLICK_STATE_DIR", str(tmp_path))
        els = [_FakeEl("a", [0, 0, 10, 10]),
               _FakeEl("b", None),              # 没有坐标，不该占号
               _FakeEl("c", [0, 0, 5, 5])]
        mapping = refstore.assign(els, 123)
        assert list(mapping) == ["@e1", "@e2"], "没坐标的元素不能占编号"
        assert mapping["@e2"]["name"] == "c"

    def test_tree_numbering_matches_saved_numbering(self, tmp_path, monkeypatch):
        """describe_tree 显示的编号必须和 assign 存下来的完全一致。"""
        from bgkit.bgclick import refstore
        monkeypatch.setenv("BGCLICK_STATE_DIR", str(tmp_path))
        els = [_FakeEl("a", [0, 0, 10, 10]), _FakeEl("b", None),
               _FakeEl("c", [0, 0, 5, 5]), _FakeEl("d", [0, 0, 3, 3])]
        shown = [(ref, el.name) for ref, el in refstore.iter_refs(els)]
        saved = list(refstore.assign(els, 123))
        assert [r for r, _ in shown if r] == saved
        assert shown[1][0] is None

    def test_roundtrip_save_and_resolve(self, tmp_path, monkeypatch):
        from bgkit.bgclick import refstore
        monkeypatch.setenv("BGCLICK_STATE_DIR", str(tmp_path))
        import ctypes
        from bgkit.bgclick.win32 import user32
        # 自己这个进程的主窗口不一定有，拿桌面窗口的句柄来验「窗口存在」这一关
        hwnd = user32.GetDesktopWindow()
        refstore.save(hwnd, refstore.assign([_FakeEl("ok", [0, 0, 20, 20])], hwnd))
        hit = refstore.resolve("@e1", allow_stale=True)
        assert hit["hwnd"] == hwnd
        assert hit["name"] == "ok"
        assert hit["client_center"] == [-90, -90]

    def test_resolve_without_any_scan(self, tmp_path, monkeypatch):
        from bgkit.bgclick import refstore
        monkeypatch.setenv("BGCLICK_STATE_DIR", str(tmp_path))
        with pytest.raises(refstore.RefError, match="还没有任何扫描记录"):
            refstore.resolve("@e1")

    def test_resolve_unknown_ref_lists_available(self, tmp_path, monkeypatch):
        from bgkit.bgclick import refstore
        from bgkit.bgclick.win32 import user32
        monkeypatch.setenv("BGCLICK_STATE_DIR", str(tmp_path))
        hwnd = user32.GetDesktopWindow()
        refstore.save(hwnd, refstore.assign([_FakeEl("a", [0, 0, 9, 9])], hwnd))
        with pytest.raises(refstore.RefError, match="不在上次扫描结果里"):
            refstore.resolve("@e7")

    def test_resolve_dead_window(self, tmp_path, monkeypatch):
        from bgkit.bgclick import refstore
        monkeypatch.setenv("BGCLICK_STATE_DIR", str(tmp_path))
        refstore.save(0xDEADBEEF, refstore.assign([_FakeEl("a", [0, 0, 9, 9])], 0xDEADBEEF))
        with pytest.raises(refstore.RefError, match="已经不存在"):
            refstore.resolve("@e1", allow_stale=True)


class TestMcpArgNormalize:
    """MCP 工具参数 → 服务端字段。这类字段名对不上是最隐蔽的错：
    工具不报错、服务端也不报错，只是参数被静默丢掉，最后点了错误的位置。"""

    def test_click_xy_becomes_pos(self):
        from bgkit.bgmcp.server import _normalize_args
        out = _normalize_args("click", {"hwnd": "0x1", "x": 700, "y": 500})
        assert out["pos"] == [700, 500]
        assert "x" not in out and "y" not in out

    def test_click_explicit_pos_wins(self):
        from bgkit.bgmcp.server import _normalize_args
        out = _normalize_args("click", {"pos": [1, 2], "x": 700, "y": 500})
        assert out["pos"] == [1, 2]

    def test_click_single_axis_is_rejected(self):
        from bgkit.bgmcp.server import _normalize_args
        with pytest.raises(ValueError, match="必须同时给"):
            _normalize_args("click", {"x": 700})

    def test_click_ref_untouched(self):
        from bgkit.bgmcp.server import _normalize_args
        out = _normalize_args("click", {"ref": "@e3"})
        assert out == {"ref": "@e3"}

    def test_mouse_delta_alias_to_scroll(self):
        from bgkit.bgmcp.server import _normalize_args
        out = _normalize_args("mouse", {"delta": -5})
        assert out["scroll"] == -5
        assert "delta" not in out

    def test_mouse_scroll_not_overridden_by_delta(self):
        from bgkit.bgmcp.server import _normalize_args
        out = _normalize_args("mouse", {"scroll": 3, "delta": -5})
        assert out["scroll"] == 3

    def test_mouse_dead_mode_dropped(self):
        from bgkit.bgmcp.server import _normalize_args
        out = _normalize_args("mouse", {"mode": "wheel", "scroll": -2})
        assert "mode" not in out
        assert out["scroll"] == -2

    def test_mouse_schema_matches_server_fields(self):
        """schema 里声明的字段必须都是服务端真认的。"""
        from bgkit.bgmcp.server import TOOLS
        props = next(t for t in TOOLS if t["name"] == "mouse")["inputSchema"]["properties"]
        for dead in ("delta", "mode"):
            assert dead not in props, f"{dead} 服务端不认，不该出现在 schema 里"
        for live in ("dx", "dy", "to", "path", "pattern", "distance",
                     "scroll", "axis", "from", "button"):
            assert live in props, f"{live} 是服务端真字段，schema 里缺了"
