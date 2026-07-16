# -*- coding: utf-8 -*-
"""复习笔记 Markdown -> PDF（经 xelatex + tcolorbox）。

流程：检测宏包 -> 读 MD（剥离首行 # 作为标题）-> md2tex 转换 -> 套模板写 .tex
     -> xelatex 编译 2 遍（目录）-> 产出 PDF。
失败不抛崩：捕获 xelatex 错误并打印，PDF 缺失则返回 False。
"""
from __future__ import annotations
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import md2tex

# Windows 下让 xelatex/kpsewhich 等控制台子进程静默运行（不弹黑窗）；非 Windows 为 0，无副作用。
# 注意：capture_output 只重定向标准输出，挡不住 Windows 为控制台程序自动分配的可见窗口。
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

REQUIRED_PACKAGES = ["ctex.sty", "tcolorbox.sty", "fancyhdr.sty", "listings.sty",
                     "titlesec.sty", "tabularx.sty", "hyperref.sty",
                     "mathrsfs.sty", "mathtools.sty", "pdfcol.sty"]

# 黄骏斌模板配色（PyMuPDF 实测）
PREAMBLE = r"""% !TEX program = xelatex
\documentclass[a4paper,11pt,fontset=windows]{ctexart}
\usepackage{geometry}
\geometry{a4paper,top=2.4cm,bottom=2.4cm,left=2.2cm,right=2.2cm}
\usepackage{amsmath}
\usepackage{amssymb}   % \mathbb{R} \mathbb{P} 等
\usepackage{mathrsfs}  % \mathscr{F} 花体（σ-代数等）
\usepackage{mathtools}
\usepackage{amsfonts}
\usepackage{xcolor}
% 模板色
\definecolor{purpleframe}{rgb}{0.36,0.20,0.46}
\definecolor{lavender}{rgb}{0.97,0.96,0.98}
\definecolor{titleblue}{rgb}{0.12,0.53,0.90}
\definecolor{lightgray}{rgb}{0.95,0.95,0.95}
% 标记色
\definecolor{defblue}{rgb}{0.20,0.40,0.70}
\definecolor{errred}{rgb}{0.80,0.20,0.20}
\definecolor{imporg}{rgb}{0.90,0.55,0.10}
\definecolor{mustpurple}{rgb}{0.45,0.20,0.55}
\definecolor{freqgreen}{rgb}{0.20,0.60,0.30}
\definecolor{realteal}{rgb}{0.15,0.50,0.55}
\definecolor{memgray}{rgb}{0.40,0.40,0.40}
\definecolor{stepbrown}{rgb}{0.50,0.35,0.20}

% 彩色小标签
\newcommand{\mk}[2]{{\setlength{\fboxsep}{2pt}\colorbox{#1!18}{\textcolor{#1}{\small\bfseries #2}}}}

% 兼容：本机 LaTeX 内核较旧，缺 PDF 结构标签命令；做空实现以免 tcolorbox 6.x 报错
\providecommand{\NewStructureName}[1]{}
\providecommand{\AssignStructureRole}[2]{}
\providecommand{\NewTagging}[2]{}
\providecommand{\tagstructbegin}[1]{}
\providecommand{\tagstructend}{}
\providecommand{\tagmcbegin}[1]{}
\providecommand{\tagmcend}{}
\providecommand{\tagmcendchunk}{}
\providecommand{\tagPDFAction}[2]{}
\usepackage{tcolorbox}
\tcbuselibrary{breakable}
% 例题外盒：深紫框 + 浅紫底（标题条为紫色）
\newtcolorbox{examplebox}[1]{
  colback=lavender,colframe=purpleframe,boxrule=0.8pt,arc=2pt,breakable,
  left=6pt,right=6pt,top=5pt,bottom=5pt,
  title={#1},fonttitle=\bfseries,coltitle=white,colbacktitle=purpleframe}
% 例题分段子盒：蓝标题条 + 浅灰正文
\newtcolorbox{expart}[1]{
  colback=lightgray,colframe=titleblue,boxrule=0.4pt,arc=1pt,breakable,
  left=5pt,right=5pt,top=3pt,bottom=3pt,
  title={#1},fonttitle=\bfseries\small,coltitle=white,colbacktitle=titleblue}

\usepackage{tabularx}
\usepackage{titlesec}
\titleformat{\section}{\Large\bfseries\heiti}{\thesection}{0.5em}{}
\titleformat{\subsection}{\large\bfseries\heiti}{\thesubsection}{0.5em}{}
\titleformat{\subsubsection}{\normalsize\bfseries\heiti}{\thesubsubsection}{0.5em}{}
\setcounter{secnumdepth}{-2}   % 不自动编号，但 section 仍进目录
\setcounter{tocdepth}{3}

\usepackage{listings}
\lstset{basicstyle=\ttfamily\small\linespread{1.0},
  breaklines=true,frame=single,framerule=0.3pt,rulecolor=\color{gray!50},
  backgroundcolor=\color{lightgray!40},xleftmargin=4pt,xrightmargin=2pt,
  keywordstyle=\color{titleblue}\bfseries,commentstyle=\color{gray}\itshape,
  stringstyle=\color{freqgreen},showstringspaces=false,extendedchars=true,
  aboveskip=6pt,belowskip=6pt}

\usepackage{fancyhdr}
\usepackage[colorlinks=true,linkcolor=titleblue,urlcolor=titleblue,
  bookmarksnumbered=false,pdftitle={__COURSE__}]{hyperref}
"""


def _kpsewhich_bin() -> str:
    """kpsewhich 可执行路径：优先 PATH，否则回退到 TinyTeX 默认目录（与 _xelatex 一致）。

    TinyTeX 安装脚本不会把 bin 目录加入 PATH，若直接用裸 "kpsewhich" 会 FileNotFoundError，
    导致所有宏包被误判为缺失。
    """
    if shutil.which("kpsewhich"):
        return "kpsewhich"
    cand = Path(os.environ.get("USERPROFILE", "")) / "AppData/Roaming/TinyTeX/bin/windows/kpsewhich.exe"
    return str(cand) if cand.exists() else "kpsewhich"


def _kpsewhich(pkg: str) -> bool:
    try:
        return subprocess.run([_kpsewhich_bin(), pkg], capture_output=True, text=True,
                              creationflags=CREATE_NO_WINDOW).stdout.strip() != ""
    except Exception:
        return False


def check_packages() -> list[str]:
    """返回缺失的宏包列表。"""
    return [p for p in REQUIRED_PACKAGES if not _kpsewhich(p)]


def _xelatex() -> str:
    """返回 xelatex 可执行路径。"""
    if shutil.which("xelatex"):
        return "xelatex"
    cand = Path(os.environ.get("USERPROFILE", "")) / "AppData/Roaming/TinyTeX/bin/windows/xelatex.exe"
    return str(cand) if cand.exists() else "xelatex"


def _build_tex_source(body_tex: str, course: str) -> str:
    head = PREAMBLE.replace("__COURSE__", course.replace("\\", ""))
    return (head + "\n\\begin{document}\n"
            + "\\title{" + course + "}\n\\date{}\n\\maketitle\n"
            + "\\thispagestyle{fancy}\n"
            + "\\fancyhf{}\n\\fancyhead[L]{\\small " + course
            + "}\\fancyhead[R]{\\small 复习笔记}\\fancyfoot[C]{\\thepage}\n"
            + "\\renewcommand{\\headrulewidth}{0.4pt}\n"
            + "\\tableofcontents\n\\newpage\n"
            + body_tex + "\n\\end{document}\n")


def _extract_title(md: str, fallback: str):
    """剥离首行 '# ' 作为标题，返回 (title, 剩余MD)。"""
    lines = md.splitlines()
    if lines and re.match(r"^#\s+", lines[0]):
        title = re.sub(r"^#\s+", "", lines[0]).strip()
        return title, "\n".join(lines[1:])
    return fallback, md


def build_pdf(md_path: Path, pdf_path: Path, course: str | None = None) -> bool:
    missing = check_packages()
    if missing:
        print("[pdf] 缺少 LaTeX 宏包（PDF 导出需要）：")
        print(f"  缺失：{', '.join(missing)}")
        print("  修复：运行 `py setup.py` 会自动用 tlmgr 补齐；或参考 README「PDF 导出需要 LaTeX」。")
        return False

    md = Path(md_path).read_text(encoding="utf-8")
    # 即时清理：删掉旧 MD 里写死的 公式汇总/易错点汇总/高频考点汇总/考前速查 段
    # （新 render_final 已不输出这些；此处保证已有 MD 转 PDF 时也干净）
    md = re.sub(r"## 四、全课程公式汇总.*?(?=## |$)", "", md, flags=re.S)
    md = re.sub(r"## 五、全课程易错点汇总.*?(?=## |$)", "", md, flags=re.S)
    md = re.sub(r"## 六、全课程高频考点汇总.*?(?=## |$)", "", md, flags=re.S)
    md = re.sub(r"## 七、考前速查手册.*?(?=## |$)", "", md, flags=re.S)
    title, rest = _extract_title(md, course or "复习笔记")
    course = course or title
    body = md2tex.md_to_tex(rest)
    tex_src = _build_tex_source(body, course)

    with tempfile.TemporaryDirectory(prefix="rvnote_tex_") as td:
        td = Path(td)
        texf = td / "review.tex"
        texf.write_text(tex_src, encoding="utf-8")
        exe = _xelatex()
        ok = True
        for _ in range(2):  # 两遍：目录/交叉引用
            r = subprocess.run([exe, "-interaction=nonstopmode",
                                "-output-directory", str(td), str(texf)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               creationflags=CREATE_NO_WINDOW)
            if r.returncode != 0:
                ok = False
                _report_error(r, td)
                break
        pdf_prod = td / "review.pdf"
        if ok and pdf_prod.exists():
            shutil.copy(pdf_prod, pdf_path)
            print(f"[pdf] 已生成 {pdf_path}")
            return True
        if ok and not pdf_prod.exists():
            print("[pdf] xelatex 返回成功但未找到 PDF，请查看日志。")
        return False


def _report_error(r: subprocess.CompletedProcess, td: Path):
    log = (td / "review.log")
    print("[pdf] xelatex 编译失败。关键错误：")
    shown = 0
    if log.exists():
        for ln in log.read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.startswith("!") or "Error" in ln or "Undefined" in ln:
                print("    " + ln.strip()[:200]); shown += 1
            if shown > 15:
                break
    if shown == 0:
        for ln in (r.stdout or "").splitlines()[-30:]:
            print("    " + ln[:200])
    print(f"[pdf] 完整日志见 {td} （临时目录）。可手动编译 review.tex 排查。")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="复习笔记 MD -> PDF")
    ap.add_argument("md", help="输入 Markdown 路径")
    ap.add_argument("-o", "--output", help="输出 PDF 路径")
    ap.add_argument("--course", default="复习笔记", help="课程名（页眉/标题）")
    args = ap.parse_args()
    md = Path(args.md)
    out = Path(args.output) if args.output else md.with_suffix(".pdf")
    ok = build_pdf(md, out, args.course)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
