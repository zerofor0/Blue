# -*- coding: utf-8 -*-
"""复习笔记助手 · 桌面启动器（PyWebView 原生窗口 + Flask 后台）。

用法：
  python launch.py        # 开发调试：原生窗口（无 pywebview 时退回浏览器）
打包：
  见 build_exe.bat / review_notes.spec，产出 dist/复习笔记助手.exe
"""
from __future__ import annotations
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as appmod  # noqa: E402


def wait_for_server(url: str, timeout: float = 15.0):
    """轮询直到 Flask 起来。"""
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.15)
    return False


def main():
    # 锚定工作目录到 exe 旁（frozen）或项目根（CLI），保证相对路径一致
    os.chdir(appmod.config.app_root())

    port = appmod.find_free_port()
    appmod.PORT = port
    url = f"http://127.0.0.1:{port}/"

    server = threading.Thread(
        target=appmod.app.run,
        kwargs=dict(host="127.0.0.1", port=port, threaded=True, debug=False, use_reloader=False),
        daemon=True,
    )
    server.start()

    if not wait_for_server(url):
        print("[gui] 后台启动超时，请在浏览器手动打开", url)
        input("按回车退出...")
        return

    # 优先用 PyWebView 打开原生窗口；缺失则退回系统浏览器。
    try:
        import webview  # pywebview
        webview.create_window(
            "复习笔记助手",
            url,
            width=1180, height=820,
            min_size=(900, 640),
            text_select=True,
        )
        # 窗口关闭后退出整个进程
        webview.start()
        sys.exit(0)
    except ImportError:
        print("[gui] 未安装 pywebview，已在系统浏览器打开。安装后体验更佳：pip install pywebview")
        webbrowser.open(url)
        print("后台运行中，关闭此窗口或按 Ctrl+C 退出。")
        try:
            while server.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
