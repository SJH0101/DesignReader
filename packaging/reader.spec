# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 명세 — DesignReader.app"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
APP_NAME = "DesignReader"

hidden = (collect_submodules("uvicorn")
          + collect_submodules("webview")
          + ["app.server", "app.db", "app.ai", "app.prompts",
             "anyio._backends._asyncio"])

datas = [
    (str(ROOT / "app" / "static"), "app/static"),
    # 교재는 넣지 않는다. 쓰는 사람이 자기 PDF 를 넣으면 그때 처리된다.
    (str(ROOT / "packaging" / "build" / "seed.db"), "data"),
]

a = Analysis(
    [str(ROOT / "app" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PIL", "numpy", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="DesignReader",   # 내부 실행 파일은 ASCII (codesign이 한글명에서 실패)
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="DesignReader",
)

app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=str(ROOT / "packaging" / "build" / "icon.icns"),
    bundle_identifier="com.junhyeoksong.diyeokyeon.reader",
    info_plist={
        "CFBundleName": APP_NAME,
        "CFBundleExecutable": "DesignReader",
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "NSHumanReadableCopyright": "개인 학습용",
    },
)
