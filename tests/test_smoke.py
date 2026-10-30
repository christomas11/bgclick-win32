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


def _find_notepad(timeout: float = 15.0):
    """轮询找记事本窗口，找不到返回 None（不抛异常，交给 skip）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            for hwnd in bc.iter_top_level_windows():
                try:
                    name = (bc.process_name(bc.window_pid(hwnd)) or "").lower()
                except Exception:
                    continue
                if "notepad" in name:
                    return hwnd
        except Exception:
            return None
        time.sleep(0.25)
    return None


@pytest.fixture()
def notepad():
    if os.name != "nt":
        pytest.skip("仅 Windows")
    try:
        proc = subprocess.Popen(["notepad.exe"],
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except Exception as e:
        pytest.skip(f"起不了记事本：{e}")

    hwnd = _find_notepad()
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

    def test_screenshot_written(self, notepad, tmp_path):
        out = tmp_path / "notepad.png"
        info = bc.save_screenshot(notepad, str(out))
        assert os.path.exists(info["path"]), info
        assert os.path.getsize(info["path"]) > 1000, "截图文件小得不像话"
        with open(info["path"], "rb") as f:
            assert f.read(8) == b"\x89PNG\r\n\x1a\n"
