# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：复习笔记助手 → 单个 exe。

打包：
  完整版（含图片 OCR）：  py -m PyInstaller --clean --noconfirm review_notes.spec
  精简版（无 OCR，更小）： set RN_LITE=1 && py -m PyInstaller --clean --noconfirm review_notes.spec
产物： dist/<name>.exe （单文件），build_exe.bat 会把它移到项目根。

体积参考（实测）：
  完整版 ~134 MB；精简版（去掉 onnxruntime + RapidOCR）~70-80 MB；
  若另装 UPX，两者可再压缩约 30%。

调试： 把下方 console=False 改 True 可看到后台日志。
"""
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_dynamic_libs

# RN_LITE=1 → 不打包 OCR（onnxruntime + RapidOCR），体积大幅缩小；
# 代价：图片、扫描版 PDF 不会被 OCR（文本型 PDF / PPT / Word 提取不受影响）。
LITE = os.environ.get("RN_LITE") == "1"

# ---- 数据文件 ----
datas = [("gui", "gui")]                       # GUI 页面
datas += collect_data_files("pptx")            # python-pptx 默认模板
datas += collect_data_files("docx")            # python-docx 默认样式
if not LITE:
    datas += collect_data_files("rapidocr_onnxruntime")  # RapidOCR 模型/配置

# ---- 动态库 ----
binaries = []
if not LITE:
    binaries += collect_dynamic_libs("onnxruntime")   # onnxruntime 的 DLL（体积大头）

# ---- 隐式导入 ----
hiddenimports = [
    # 本项目模块（部分在函数内 import，需显式声明）
    "config", "pipeline", "extractors", "prompts", "llm", "md2tex", "build_pdf",
    "agent", "run", "setup", "app",
    # Web 栈
    "flask", "jinja2", "werkzeug", "itsdangerous", "click", "markupsafe",
    # 原生窗口
    "webview", "webview.platforms", "webview.platforms.edgechromium",
    "webview.platforms.winforms", "clr",
    # 提取 / 模型
    "openai", "pptx", "docx", "fitz",
]
if not LITE:
    hiddenimports += ["rapidocr_onnxruntime", "onnxruntime"]
hiddenimports += collect_submodules("openai")
hiddenimports += collect_submodules("pywebview")

# ---- 排除（减体积）----
excludes = ["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6",
            "matplotlib", "IPython", "notebook", "pytest", "setuptools", "pip"]
if LITE:
    excludes += ["rapidocr_onnxruntime", "onnxruntime", "onnxruntime.capi"]

a = Analysis(
    ["launch.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="复习笔记助手" if not LITE else "复习笔记助手-lite",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                                  # 需另装 UPX 才生效；装了可压缩约 30%
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                             # 不显示黑窗；调试改 True
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="gui/app.ico",                      # 有图标时取消注释
)
