# -*- coding: utf-8 -*-
"""bgkit.bgclick.screen.py —— 截图：PrintWindow 后台抓图 + BMP/PNG 编码

从 bgclick.py 拆出的一节（源文件第 2425-2479, 2489-2632 行）。只做代码搬运，逻辑未改。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import struct
from .errors import AppError
from .geometry import window_dimensions
from .win32 import user32



# --------------------------------------------------------------------------
# 截图（PrintWindow：后台抓图，不需要窗口在前台，也不抢焦点）
# --------------------------------------------------------------------------

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

PW_CLIENTONLY = 0x00000001
PW_RENDERFULLCONTENT = 0x00000002
DIB_RGB_COLORS = 0
BI_RGB = 0
SRCCOPY = 0x00CC0020

user32.GetWindowDC.argtypes = [wintypes.HWND]
user32.GetWindowDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL

gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
gdi32.GetDIBits.restype = ctypes.c_int


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]




# HGDI_ERROR 是一个"伪句柄"，SelectObject 失败时返回它（等于 (HGDIOBJ)-1）
_HGDI_ERROR = ctypes.c_void_p(-1).value


def capture_window_rgb(hwnd: int, client_only: bool = False,
                       method: str = "auto") -> tuple[int, int, bytes, str]:
    """
    抓取窗口像素。返回 (宽, 高, RGB 字节(自上而下, 每行 w*3), 实际用的方法)。

    为什么用 PrintWindow：它让窗口**自己**把自己画到我们的内存 DC 上，
    所以窗口被别的窗口盖住、甚至不在前台，也照样能抓到 —— 而且不抢焦点。

    method:
      auto  —— 先试 PrintWindow(PW_RENDERFULLCONTENT)，失败退到普通 PrintWindow，
               再失败退到 BitBlt（这条要求窗口可见且未被遮挡）
      print —— 只用 PrintWindow
      bitblt—— 直接从窗口 DC 拷贝（最快，但被遮挡就抓到别人的画面）
    """
    w, h = window_dimensions(hwnd, client_only)
    if w <= 0 or h <= 0:
        raise AppError(f"窗口尺寸无效：{w}x{h}（窗口可能已最小化或已关闭）", code=1)
    if w > 20000 or h > 20000:
        raise AppError(f"窗口尺寸过大：{w}x{h}", code=2)

    src_dc = user32.GetWindowDC(hwnd)
    if not src_dc:
        raise AppError(f"GetWindowDC 失败：{ctypes.get_last_error()}", code=5)
    mem_dc = gdi32.CreateCompatibleDC(src_dc)
    bmp = gdi32.CreateCompatibleBitmap(src_dc, w, h)
    old_obj = gdi32.SelectObject(mem_dc, bmp)
    used = "none"

    # SelectObject 失败返回 NULL(0) 或 HGDI_ERROR((HGDIOBJ)-1)，两者都要当作"没换成功"
    if not old_obj or old_obj == _HGDI_ERROR:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, src_dc)
        raise AppError(f"SelectObject 失败：{ctypes.get_last_error()}", code=5)

    try:
        if method in ("auto", "print"):
            flags = PW_RENDERFULLCONTENT | (PW_CLIENTONLY if client_only else 0)
            ok = user32.PrintWindow(hwnd, mem_dc, flags)
            if ok:
                used = "printwindow-fullcontent"
            else:
                flags = PW_CLIENTONLY if client_only else 0
                if user32.PrintWindow(hwnd, mem_dc, flags):
                    used = "printwindow"
                elif method == "print":
                    raise AppError("PrintWindow 失败（窗口可能被保护或已最小化）", code=5)
        if used == "none":
            if not gdi32.BitBlt(mem_dc, 0, 0, w, h, src_dc, 0, 0, SRCCOPY):
                raise AppError(f"BitBlt 失败：{ctypes.get_last_error()}", code=5)
            used = "bitblt"

        # 取像素：32 位 BGRA，自下而上
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h          # 负数 = 自上而下，省一次翻转
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        got = gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)
        if got == 0:
            raise AppError(f"GetDIBits 失败：{ctypes.get_last_error()}", code=5)

        # BGRA -> RGB（顺手丢掉 alpha，它在这个场景里没用）
        raw = buf.raw[:w * h * 4]
        rgb = bytearray(w * h * 3)
        rgb[0::3] = raw[2::4]   # R
        rgb[1::3] = raw[1::4]   # G
        rgb[2::3] = raw[0::4]   # B
        return w, h, bytes(rgb), used
    finally:
        # old_obj 已在上面确认过是有效句柄，可以安全换回去
        if old_obj and old_obj != _HGDI_ERROR:
            gdi32.SelectObject(mem_dc, old_obj)
        if bmp:
            gdi32.DeleteObject(bmp)
        if mem_dc:
            gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, src_dc)


# --- 极简 PNG 编码（纯标准库 zlib，不依赖 Pillow）---

def _png_chunk(tag: bytes, data: bytes) -> bytes:
    import zlib as _z
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", _z.crc32(tag + data) & 0xFFFFFFFF))


def encode_png_rgb(width: int, height: int, rgb: bytes, level: int = 6) -> bytes:
    """把自上而下的 RGB 原始像素编码成 PNG 字节流。"""
    import zlib as _z
    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)                                  # 每行滤波器类型 0（None）
        raw += rgb[y * stride:(y + 1) * stride]

    ihdr = (struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))  # 8bit, truecolor RGB
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", _z.compress(bytes(raw), level))
            + _png_chunk(b"IEND", b""))


def encode_bmp_rgb(width: int, height: int, rgb: bytes) -> bytes:
    """编码成 BMP（有些老工具吃 BMP 不吃 PNG）。24 位，自下而上。"""
    stride = (width * 3 + 3) & ~3
    pad = stride - width * 3
    rows = bytearray()
    for y in range(height - 1, -1, -1):                # BMP 是自下而上
        row = rgb[y * width * 3:(y + 1) * width * 3]
        for i in range(0, len(row), 3):                # RGB -> BGR
            rows += bytes((row[i + 2], row[i + 1], row[i]))
        rows += b"\x00" * pad
    pixels = bytes(rows)
    header = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(pixels), 0, 0, 14 + 40)
    dib = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(pixels),
                      2835, 2835, 0, 0)
    return header + dib + pixels


def save_screenshot(hwnd: int, path: str, client_only: bool = False,
                    fmt: str = "png", method: str = "auto") -> dict:
    """抓图并存盘。返回元信息（含实际尺寸、格式、方法）。"""
    w, h, rgb, used = capture_window_rgb(hwnd, client_only=client_only, method=method)
    data = encode_bmp_rgb(w, h, rgb) if fmt.lower() == "bmp" else encode_png_rgb(w, h, rgb)

    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return {"path": os.path.abspath(path), "width": w, "height": h,
            "format": fmt.lower(), "bytes": len(data), "capture_method": used,
            "client_only": client_only}
