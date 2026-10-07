# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— 生成 onedir 形式的 bgserver.exe。

用法（在仓库根目录）：

    pyinstaller bgserver.spec --noconfirm

产物：dist/bgserver/bgserver.exe + dist/bgserver/_internal/
★ onedir 不能只搬 exe，_internal 必须跟着一起走。

路径刻意写成相对形式（不用 SPECPATH 拼绝对路径），
这样仓库拷到任何目录、任何机器上都能直接打包。
"""
from PyInstaller.utils.hooks import collect_submodules

# bgkit 下面有四个子包，PyInstaller 的静态分析追不全，靠 collect_submodules 全收
hiddenimports = []
hiddenimports += collect_submodules('bgkit')


a = Analysis(
    ['bgserver.py'],
    pathex=[],
    binaries=[],
    datas=[('bgclick.ico', '.')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='bgserver',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['bgclick.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='bgserver',
)
