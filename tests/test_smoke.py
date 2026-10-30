# -*- coding: utf-8 -*-
"""冒烟测试 —— 开一个真记事本，把「探针 → 点击 → 打字 → 截图」四条链路跑一遍。

为什么单独一个文件：
    CI 的 windows-latest 有时没有交互式桌面 session，记事本起得来但窗口不出现，
    这类用例必须能整组跳过，不能把流水线染红。所以全部挂在 smoke 标记下。

本地跑法： pytest -m smoke
"""

from __future__ import annotations

import os
import subprocess
import time

import pytest

from bgkit import bgclick as bc

pytestmark = pytest.mark.smoke

WM_CHAR = 0x0102


def _find_notepad(timeout: float = 15.0, want_pid: int = 0):
    """轮询找记事本窗口，找不到返回 None（不抛异常，交给 skip）。

    给了 want_pid 就只认这个进程的窗口 —— 机器上本来开着记事本时，
    光按进程名匹配会拿到别人的窗口，后面对它截图/打字就全乱了。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            for hwnd in bc.iter_top_level_windows():
                try:
                    pid = bc.window_pid(hwnd)
                    name = (bc.process_name(pid) or "").lower()
                except Exception:
                    continue
                if "notepad" not in name:
                    continue
                if want_pid and pid != want_pid:
                    continue
                return hwnd
        except Exception:
            return None
        time.sleep(0.25)
    return None


@pytest.fixture(scope="module")
def notepad():
    """整个文件共用一个记事本窗口 —— 每条用例重开一次太慢，也没必要。"""
    if os.name != "nt":
        pytest.skip("仅 Windows")
    try:
        proc = subprocess.Popen(["notepad.exe"],
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except Exception as e:
        pytest.skip(f"起不了记事本：{e}")

    # 先按 pid 找（自己起的那个），找不到再按进程名兜底：
    # Win11 的 notepad.exe 是启动器，新进程可能被转交给已有实例，pid 对不上。
    hwnd = _find_notepad(timeout=4, want_pid=proc.pid) or _find_notepad(timeout=8)
    if not hwnd:
        proc.terminate()
        pytest.skip("没等到记事本窗口（可能没有交互式桌面 session）")

    yield hwnd

    # 强杀，不要触发「是否保存」对话框
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


class TestNotepadSmoke:
    def test_window_described(self, notepad):
        info = bc.describe(notepad)
        assert info["hwnd"] == notepad
        assert "notepad" in info["process"].lower()
        cw, ch = info["client_size"]
        assert cw > 0 and ch > 0

    def test_probe_returns_bool(self, notepad):
        # 记事本是普通权限进程，探针应该能通过
        pr = bc.probe_window(notepad)
        assert isinstance(pr["ok"], bool)
        assert pr["ok"] is True, f"记事本居然投不进去：{bc.describe_error(pr['error'])}"

    def test_post_click_succeeds(self, notepad):
        # 点客户区左上角一点，不动光标、不抢焦点
        rec = bc.send_click_post(notepad, 20, 20)
        assert rec["ok"] is True, rec
        assert rec["method"] == "post"

    def test_post_text_succeeds(self, notepad):
        text = "bgclick smoke 测试"
        sent = 0
        for ch in text:
            ok, err = bc.post_checked(notepad, WM_CHAR, ord(ch), 0)
            assert ok, f"第 {sent} 个字符投递失败：{bc.describe_error(err)}"
            sent += 1
        assert sent == len(text)

    def test_screenshot_written(self, notepad, tmp_path_factory):
        out = tmp_path_factory.mktemp("shot") / "notepad.png"
        info = bc.save_screenshot(notepad, str(out))
        assert os.path.exists(info["path"]), info
        assert os.path.getsize(info["path"]) > 1000, "截图文件小得不像话"
        with open(info["path"], "rb") as f:
            assert f.read(8) == b"\x89PNG\r\n\x1a\n"


class TestWait:
    """--wait 的退出码是脚本串联的依据，不能错。"""

    def test_returns_zero_when_window_exists(self, notepad):
        from bgkit.bgclick.cli import build_parser, cmd_wait
        args = build_parser().parse_args(
            ["--wait", "--hwnd", hex(notepad), "--timeout", "3"])
        assert cmd_wait(args) == 0

    def test_times_out_with_code_6(self):
        from bgkit.bgclick.cli import EXIT_WAIT_TIMEOUT, build_parser, cmd_wait
        args = build_parser().parse_args(
            ["--wait", "--title", "绝不存在的窗口zzz", "--timeout", "0.6",
             "--interval", "0.1"])
        assert cmd_wait(args) == EXIT_WAIT_TIMEOUT

    def test_gone_returns_immediately_when_absent(self):
        from bgkit.bgclick.cli import build_parser, cmd_wait
        args = build_parser().parse_args(
            ["--wait", "--gone", "--title", "绝不存在的窗口zzz",
             "--timeout", "2", "--interval", "0.1"])
        assert cmd_wait(args) == 0
