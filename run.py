# -*- coding: utf-8 -*-
"""一键整理向导。

用法：
  1) 把课件（PPT/PDF/DOCX/图片）复制到同目录的 input 文件夹
  2) 双击 [整理笔记.bat]，或运行 py run.py
  3) 按提示输入课程名，结果输出到 复习笔记.md
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import config

ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "input"
SUPPORTED = {".pptx", ".ppt", ".pdf", ".docx", ".doc",
             ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}


def _ask(prompt, default=""):
    try:
        s = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        s = ""
    return s or default


def list_courseware():
    if not INPUT.exists():
        return []
    return [p for p in INPUT.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED]


def main():
    print("=" * 54)
    print("   复习笔记整理 - 一键向导")
    print("=" * 54)

    # 1) 检查 API 是否已配置（REVIEW_NO_LLM=1 可强制走 Demo，跳过检查）
    config.load_env()
    no_llm = os.getenv("REVIEW_NO_LLM") == "1"
    configured = all(os.getenv(k) for k in
                     ("REVIEW_API_BASE", "REVIEW_API_KEY", "REVIEW_MODEL"))
    if not no_llm and not configured:
        print("\n[!] 还没配置大模型 API。请先运行一次：  py setup.py")
        _ask("\n按回车退出...")
        return

    # 2) 检查 input 文件夹里的课件
    INPUT.mkdir(exist_ok=True)
    files = list_courseware()
    while not files:
        print(f"\n[!] input 文件夹里没有检测到课件。")
        print(f"    请把你的 PPT / PDF / DOCX / 图片复制到这里：")
        print(f"    {INPUT}")
        _ask("\n放好后按回车重新检测（或直接关闭窗口）...")
        files = list_courseware()
        if not files:
            print("仍未检测到课件，已退出。把课件放进 input 后再运行即可。")
            return

    print(f"\n检测到 {len(files)} 个课件：")
    for p in files:
        print(f"  - {p.name}")

    # 3) 课程名（.env 里有 REVIEW_COURSE 就作默认）
    default_course = os.getenv("REVIEW_COURSE", "")
    hint = default_course or "必填"
    course = _ask(f"\n请输入课程名[{hint}]: ", default_course)
    if not course:
        print("[!] 课程名不能为空。"); return

    # 4) 运行流水线（复用 agent.run，避免命令行中文参数问题）
    out = ROOT / "复习笔记.md"
    max_chars = int(os.getenv("REVIEW_MAX_CHARS", "6000"))
    print(f"\n开始整理 -> 输出 {out.name}（单分片上限 {max_chars} 字）...\n")
    import agent
    args = SimpleNamespace(
        course=course,
        discipline=os.getenv("REVIEW_DISCIPLINE", "auto"),
        chapters="",
        max_chars=max_chars,
        api_base="", api_key="", model="",
        no_llm=no_llm,
        no_cache=os.getenv("REVIEW_NO_CACHE") == "1",
        pdf=os.getenv("REVIEW_NO_PDF") != "1",   # 默认自动生成 PDF
    )
    try:
        agent.run(INPUT, out, args)
    except Exception as e:
        print(f"\n[error] 整理过程中出错：{e}")
        import traceback
        traceback.print_exc()
        _ask("\n按回车退出...")
        return

    print("\n" + "=" * 54)
    print(f"  完成！复习笔记已生成：{out}")
    print("  可用 Word/WPS/Typora 打开，或转成 PDF。")
    print("=" * 54)
    _ask("\n按回车退出...")


if __name__ == "__main__":
    main()
