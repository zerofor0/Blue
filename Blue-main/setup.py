# -*- coding: utf-8 -*-
"""复习笔记助手 - 一键环境配置向导。

运行： py setup.py

流程：检查 Python -> 安装依赖 -> 交互式配置 API（选服务商/填密钥）-> 测试连接 -> 写入 .env
配好后即可直接运行： py agent.py 你的资料目录 -o 笔记.md --course 课程名
（无需再带 --api-base/--api-key/--model）
"""
import getpass
import subprocess
import sys
import time
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
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-r", str(req), "-q"],
        )
        print("    依赖已就绪。")
    except subprocess.CalledProcessError as e:
        print(f"    [X] 安装失败：{e}")
        print("    可手动运行： py -m pip install -r requirements.txt")


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
