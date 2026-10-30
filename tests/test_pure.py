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
        assert len(TOOLS) == 9

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
