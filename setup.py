# -*- coding: utf-8 -*-
"""复习笔记助手 - 一键环境配置向导。

运行： py setup.py

流程：检查 Python -> 安装依赖 -> 交互式配置 API（选服务商/填密钥）-> 测试连接 -> 写入 .env
配好后即可直接运行： py agent.py 你的资料目录 -o 笔记.md --course 课程名
（无需再带 --api-base/--api-key/--model）
"""
import getpass
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent


def banner():
    print("=" * 56)
    print("   复习笔记助手 - 环境配置向导")
    print("=" * 56)


def step(n, msg):
    print(f"\n[{n}] {msg}")


def _ask(prompt, default=""):
    try:
        s = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        s = ""
    return s or default


def _ask_secret(prompt):
    try:
        return getpass.getpass(prompt).strip()
    except Exception:
        try:
            return input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            return ""


def check_python():
    step(1, "检查 Python")
    print(f"    版本 {sys.version.split()[0]}")
    print(f"    路径 {sys.executable}")
    if sys.version_info < (3, 9):
        print("    [!] 建议 Python 3.9 及以上")


def install_deps():
    step(2, "安装依赖（requirements.txt）")
    req = ROOT / "requirements.txt"
    try:
        subprocess.check_call (
            ["py", "-m", "pip", "install", "-r", str(req)],
        )
        print("    依赖已就绪。")
    except subprocess.CalledProcessError as e:
        print(f"    [X] 安装失败：{e}")
        print("    可手动运行： py -m pip install -r requirements.txt")
    except FileNotFoundError:
        print("    [X] 未找到 py 启动器，请确认已安装 Python Launcher (py)。")
        print("    可手动运行： py -m pip install -r requirements.txt")

    ensure_latex()


# 需通过 tlmgr 安装的包名。注意 .sty 文件名与 tlmgr 包名并不一一对应：
#   mathrsfs.sty -> jknapltx；mathtools.sty -> mathtools；pdfcol.sty -> pdfcol；
#   tabularx.sty 属基础包 tools（随 TinyTeX 自带，无需、也不可用 tlmgr install tabularx）。
# 把不存在的包名（如 tabularx、mathrsfs）写进列表，整条命令会因“未知包名”中止。
TLMGR_INSTALL_PKGS = ["ctex", "tcolorbox", "fancyhdr", "listings",
                      "titlesec", "hyperref", "jknapltx", "mathtools", "pdfcol"]


def _xelatex_available():
    """返回可用的 xelatex 路径（PATH 或 TinyTeX 默认目录）；没有则 None。"""
    exe = shutil.which("xelatex")
    if exe:
        return exe
    tt = Path(os.environ.get("USERPROFILE", "")) / "AppData/Roaming/TinyTeX/bin/windows/xelatex.exe"
    return str(tt) if tt.exists() else None


def _download(url, dest):
    """下载 url 到 dest。用浏览器 UA，避免部分站点 WAF 拦截默认的
    Python-urllib UA（会返回 HTTP 403）。urlopen 默认跟随重定向。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    })
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def ensure_latex():
    """检测 xelatex；缺失则引导安装 TinyTeX（轻量 TeX 发行版），并补齐项目所需宏包。

    PDF 导出（build_pdf.py）依赖 xelatex 及若干宏包；部分用户未安装任何 TeX
    发行版，这里做一次性检测与引导安装。所有失败均不阻断主流程。
    """
    print("\n    ▸ 检测 LaTeX（xelatex）—— PDF 导出需要")
    if _xelatex_available():
        print("    [OK] 已检测到 xelatex，跳过安装。")
        return
    print("    未检测到 xelatex。PDF 导出需要 TeX 发行版，推荐轻量的 TinyTeX。")
    if _ask("    是否自动安装 TinyTeX（约数百 MB，需几分钟）？(y/N) [N]: ", "n").lower() != "y":
        print("    跳过。可稍后手动安装：MiKTeX (https://miktex.org) 或 TinyTeX (https://yihui.org/tinytex)")
        return
    if os.name != "nt":
        print("    [X] 自动安装暂仅支持 Windows，请参考 https://yihui.org/tinytex 手动安装。")
        return
    # 直接下载并执行官方 PowerShell 安装器（真正的安装逻辑在 .ps1 里）。
    # 不走 .bat 包装：其内部 curl 用了单引号，在 Windows cmd 下会因 URL 非法而失败。
    installer = ROOT / "install-TinyTeX.ps1"
    url = "https://tinytex.yihui.org/install-bin-windows.ps1"
    try:
        print("    正在下载 TinyTeX 安装脚本……")
        _download(url, str(installer))
        print("    正在安装 TinyTeX（从 GitHub 拉取约百兆，请耐心等待，切勿关闭窗口）……")
        subprocess.check_call(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer)])
    except subprocess.CalledProcessError as e:
        print(f"    [X] TinyTeX 安装失败：{e}")
        print("    可手动安装：MiKTeX (https://miktex.org) 或 TinyTeX (https://yihui.org/tinytex)")
        return
    except OSError as e:  # 下载失败（URLError/HTTPError 均为 OSError 子类）
        print(f"    [X] 下载失败：{e}")
        print("    可手动安装：MiKTeX (https://miktex.org) 或 TinyTeX (https://yihui.org/tinytex)")
        return
    finally:
        try:
            installer.unlink(missing_ok=True)
        except Exception:
            pass
    # 真正校验：xelatex 是否就位（返回码并不可靠——.bat 包装即便中途 curl 失败也会返回 0）
    if not _xelatex_available():
        print("    [X] TinyTeX 似乎未安装成功（未找到 xelatex）。")
        print("    可手动安装：MiKTeX (https://miktex.org) 或 TinyTeX (https://yihui.org/tinytex)")
        return
    # 把 TinyTeX 加入当前进程 PATH（build_pdf._xelatex() 也会查此目录）
    bindir = Path(os.environ.get("USERPROFILE", "")) / "AppData/Roaming/TinyTeX/bin/windows"
    if bindir.exists():
        os.environ["PATH"] = str(bindir) + os.pathsep + os.environ.get("PATH", "")
    print("    [OK] TinyTeX 安装完成。")
    # 补齐项目所需宏包（best-effort；失败不阻断，TinyTeX 首次编译也会自动补装）
    # 关键：Windows 上 tlmgr 是 tlmgr.bat，subprocess(shell=False) 无法直接执行 .bat
    # （CreateProcess 只自动补 .exe），必须经 cmd /c 让 cmd.exe 用 PATHEXT 解析到 .bat。
    if shutil.which("tlmgr"):
        print("    正在补齐宏包（ctex/tcolorbox 等，首次较慢）……")
        try:
            subprocess.check_call(["cmd", "/c", "tlmgr", "install"] + TLMGR_INSTALL_PKGS)
            print("    [OK] 宏包已就绪。")
        except subprocess.CalledProcessError as e:
            print(f"    [!] 部分宏包未装上（{e}），首次编译 PDF 时 TinyTeX 通常会自动补装。")
    else:
        print("    [!] 未找到 tlmgr，跳过宏包补齐；首次编译 PDF 时 TinyTeX 通常会自动补装。")
    print("    提示：xelatex 由 build_pdf 按 TinyTeX 目录自动定位，无需手改 PATH；")
    print("          若想在终端直接敲 xelatex，把 %APPDATA%\\TinyTeX\\bin\\windows 加入 PATH。")


def configure():
    step(3, "配置 API")
    cur = {
        "REVIEW_API_BASE": config.read_env("REVIEW_API_BASE"),
        "REVIEW_API_KEY":  config.read_env("REVIEW_API_KEY"),
        "REVIEW_MODEL":    config.read_env("REVIEW_MODEL"),
    }
    if cur["REVIEW_API_BASE"] and cur["REVIEW_API_KEY"] and cur["REVIEW_MODEL"]:
        masked = cur["REVIEW_API_KEY"]
        masked = (masked[:4] + "***" + masked[-2:]) if len(masked) > 6 else "***"
        print(f"    已有配置：base={cur['REVIEW_API_BASE']}  model={cur['REVIEW_MODEL']}  key={masked}")
        if _ask("    重新配置？(y/N) [N]: ", "n").lower() != "y":
            return cur

    print("\n    选择服务商（仅用于预填 base_url 和默认模型，密钥仍由你输入）：")
    for k, (name, base, model) in config.PROVIDERS.items():
        print(f"      {k}) {name:<18} ({model})")
    print("      6) 自定义（手动填 base_url）")

    choice = _ask("\n    选择 [1]: ", "1")
    if choice in config.PROVIDERS:
        _, base, model = config.PROVIDERS[choice]
        base = _ask(f"    base_url [{base}]: ", base)
        model = _ask(f"    模型名 [{model}]: ", model)
    else:
        base = _ask("    base_url: ", cur["REVIEW_API_BASE"])
        model = _ask("    模型名: ", cur["REVIEW_MODEL"])

    key = _ask_secret("    API Key（输入不回显）: ")
    if not key:
        print("    未输入 Key，保留原值（若有）。")
        key = cur["REVIEW_API_KEY"]
    return {"REVIEW_API_BASE": base, "REVIEW_API_KEY": key, "REVIEW_MODEL": model}


def test_connection(base, key, model):
    step(4, "测试连接")
    if not (base and key and model):
        print("    [X] 配置不完整，跳过测试。")
        return
    try:
        from openai import OpenAI
    except ImportError:
        print("    [X] openai 包未安装，请先完成第 [2] 步。")
        return
    try:
        client = OpenAI(base_url=base, api_key=key)
        t0 = time.time()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "请只回复四个汉字：连接成功"}],
            max_tokens=20,
            temperature=0,
        )
        reply = resp.choices[0].message.content.strip()
        print(f"    [OK] 连接成功（{time.time()-t0:.1f}s），模型回复：{reply}")
    except Exception as e:
        msg = str(e)
        print(f"    [X] 连接失败：{msg[:300]}")
        low = msg.lower()
        if any(x in low for x in ("401", "unauthorized", "invalid api key", "authentication")):
            print("      提示：通常是 API Key 错误或失效。")
        elif "model" in low and ("not" in low or "does not exist" in low):
            print("      提示：模型名不被该接口支持，请核对模型名。")
        elif any(x in low for x in ("connection", "timed out", "resolve", "proxy", "ssl")):
            print("      提示：网络不通或 base_url 写错（是否需要代理？）。")
        print("      提示：配置仍已保存，可修正 .env 后重跑 py setup.py。")


def main():
    banner()
    try:
        check_python()
        install_deps()
        cfg = configure()
        config.save_env(cfg)
        print(f"\n[ok] 配置已保存到 {config.ENV_FILE.name}")
        test_connection(cfg["REVIEW_API_BASE"], cfg["REVIEW_API_KEY"], cfg["REVIEW_MODEL"])
        print("\n" + "-" * 56)
        print("下一步：生成样例并整理")
        print("  py agent.py --make-sample sample_data")
        print("  py agent.py sample_data -o 笔记.md --course 课程名")
        print("-" * 56)
    except KeyboardInterrupt:
        print("\n已取消。")


if __name__ == "__main__":
    main()
