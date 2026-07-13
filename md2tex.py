# -*- coding: utf-8 -*-
"""复习笔记 Markdown -> LaTeX 正文转换器（针对本项目生成的 MD 语法）。

设计要点：
- 数学感知：$...$ / $$...$$ 整段原样透传（xelatex 原生渲染），非数学段转义 LaTeX 特殊字符。
- 结构：标题/粗体/斜体/内联代码/列表/表格/代码块 -> 对应 LaTeX。
- 标记：【定义】【易错点】【重点/必背/高频】【真题】【记忆】【解题步骤】-> 彩色 \colorbox 标签。
- 例题块：**例题** + **题目/思路/解答/技巧** -> 外层紫框 + 内嵌蓝标题条子盒（黄骏斌模板风格）。
"""
from __future__ import annotations
import re

# 标记 -> 颜色（xcolor 名称）
MARKER_COLORS = {
    "定义": "defblue", "易错点": "errred", "易错": "errred",
    "重点": "imporg", "必背": "mustpurple", "高频": "freqgreen",
    "真题": "realteal", "记忆": "memgray", "解题步骤": "stepbrown",
}


def _escape(s: str) -> str:
    """转义 LaTeX 特殊字符（用于非数学正文）。"""
    out = []
    for ch in s:
        if ch == "\\":
            out.append(r"\textbackslash{}")
        elif ch == "&":
            out.append(r"\&")
        elif ch == "%":
            out.append(r"\%")
        elif ch == "$":
            out.append(r"\$")
        elif ch == "#":
            out.append(r"\#")
        elif ch == "_":
            out.append(r"\_")
        elif ch == "{":
            out.append(r"\{")
        elif ch == "}":
            out.append(r"\}")
        elif ch == "~":
            out.append(r"\textasciitilde{}")
        elif ch == "^":
            out.append(r"\textasciicircum{}")
        else:
            out.append(ch)
    return "".join(out)


def _convert_markers(s: str) -> str:
    """把 【...】 转成彩色标签命令 \\mk{颜色}{文字}。"""
    def repl(m):
        text = m.group(1).strip()
        key = text
        color = MARKER_COLORS.get(key)
        if color is None:  # 模糊匹配，如“易错点(高频)”
            for k, c in MARKER_COLORS.items():
                if k in text:
                    color = c; break
        color = color or "defblue"
        return r"\mk{" + color + "}{" + _escape(text) + "}"
    return re.sub(r"【([^】]+)】", repl, s)


_PH = "@@PH@@"  # 占位符分隔符（纯 ASCII，正文里不会出现）


def _inline(s: str) -> str:
    """数学感知的行内转换：math/code/bold/emph/escape/markers。"""
    # 1) 抽数学（$$...$$ 先于 $...$）
    math_spans = []

    def take_math(m):
        math_spans.append(m.group(0))
        return f"{_PH}M{len(math_spans) - 1}{_PH}"

    s = re.sub(r"\$\$.*?\$\$", take_math, s, flags=re.S)
    s = re.sub(r"\$[^$\n]+?\$", take_math, s)
    # 2) 抽内联代码
    code_spans = []

    def take_code(m):
        code_spans.append(m.group(1))
        return f"{_PH}C{len(code_spans) - 1}{_PH}"

    s = re.sub(r"`([^`]+)`", take_code, s)
    # 3) 抽粗体 **...**
    bold_spans = []

    def take_bold(m):
        bold_spans.append(m.group(1))
        return f"{_PH}B{len(bold_spans) - 1}{_PH}"

    s = re.sub(r"\*\*(.+?)\*\*", take_bold, s)
    # 4) 抽斜体 *...* （单星号；不含星号）
    emph_spans = []

    def take_emph(m):
        emph_spans.append(m.group(1))
        return f"{_PH}E{len(emph_spans) - 1}{_PH}"

    s = re.sub(r"(?<!\*)\*(?!\*)([^*\n]+?)\*(?!\*)", take_emph, s)
    # 5) 转义剩余正文
    s = _escape(s)
    # 6) 标记
    s = _convert_markers(s)
    # 7) 还原：math 原样；code/bold/emph 内部各自处理。
    #    bold/emph 内部可能嵌套 math 占位符（如 **样本空间 $\Omega$**），
    #    故对其内容递归 restore。
    pat = re.compile(_PH + r"([MBCE])(\d+)" + _PH, re.S)

    def restore_once(text: str) -> str:
        def r(m):
            tag, idx = m.group(1), int(m.group(2))
            if tag == "M":
                ms = math_spans[idx]
                if ms.startswith("$$"):  # 展示公式 -> equation 环境（支持 \tag）
                    body = ms[2:-2]
                    if "\\tag" in body:
                        return "\\begin{equation}" + body + "\\end{equation}"
                    return "\\begin{equation*}" + body + "\\end{equation*}"
                return ms
            if tag == "C":
                inner = _escape(code_spans[idx])
                inner = pat.sub(r, inner) if _PH in inner else inner
                return r"\texttt{" + inner + "}"
            if tag == "B":
                # 先转义，再递归还原内部嵌套的 math 占位符，最后转标记
                inner = _convert_markers(_escape(bold_spans[idx]))
                inner = pat.sub(r, inner) if _PH in inner else inner
                return r"\textbf{" + inner + "}"
            if tag == "E":
                inner = _convert_markers(_escape(emph_spans[idx]))
                inner = pat.sub(r, inner) if _PH in inner else inner
                return r"\emph{" + inner + "}"
            return m.group(0)
        return pat.sub(r, text)

    return restore_once(s)


# --------------------------- 块级元素 ---------------------------
def _render_heading(level: int, text: str) -> str:
    t = _convert_markers(_escape(text))  # 标题里标记做纯色标签
    if level == 1:
        return r"\begin{center}{\LARGE\bfseries " + t + r"}\end{center}"
    cmds = {2: r"\section", 3: r"\subsection", 4: r"\subsubsection"}
    cmd = cmds.get(level, r"\subsubsection")
    return cmd + "{" + t + "}"


_LST_LANG = {"cpp": "C++", "c++": "C++", "c": "C", "c#": "CSharp",
             "python": "Python", "py": "Python", "java": "Java",
             "javascript": "Java", "js": "Java", "html": "HTML", "xml": "XML",
             "bash": "bash", "sh": "bash", "shell": "bash", "sql": "SQL", "tex": "TeX"}


def _render_codeblock(lines, lang) -> str:
    body = "\n".join(lines)  # lstlisting 内容原样，不转义
    lang = (lang or "").lower()
    opt = f"[language={_LST_LANG[lang]}]" if lang in _LST_LANG else ""
    return "\\begin{lstlisting}" + opt + "\n" + body + "\n\\end{lstlisting}"


def _render_table(rows) -> str:
    def split(row):
        row = row.strip()
        if row.startswith("|"):
            row = row[1:]
        if row.endswith("|"):
            row = row[:-1]
        # 按未转义的 | 分隔（保留数学里的 \|）
        cells = re.split(r"(?<!\\)\|", row)
        return [c.strip() for c in cells]
    parsed = [split(r) for r in rows if r.strip()]
    # 去掉分隔行 |---|
    parsed = [r for r in parsed if not all(set(c) <= set("-: ") and c for c in r)]
    if not parsed:
        return ""
    ncols = max(len(r) for r in parsed)
    spec = " >{\small}X" * ncols   # 每列自动换行 + 小一号字，保证不溢出
    out = [r"\par\medskip\noindent\begin{tabularx}{\linewidth}{" + spec.strip() + r"}", r"\hline"]
    for ri, r in enumerate(parsed):
        r = r + [""] * (ncols - len(r))
        out.append(" & ".join(_inline(c) for c in r) + r" \\")
        out.append(r"\hline")
    out.append(r"\end{tabularx}\par\medskip")
    return "\n".join(out)


def _is_list_item(line):
    return bool(re.match(r"^(\s*)([-*+]\s+|\d+\.\s+)", line))


def _render_list(lines) -> str:
    # 区分 bullet / numbered；支持一级缩进嵌套（简化：按缩进分层）
    out = []
    stack = []  # 缩进层级 -> 当前环境
    for line in lines:
        m = re.match(r"^(\s*)([-*+]\s+|\d+\.\s+)(.*)$", line)
        if not m:
            # 列表内续行
            if stack:
                out.append(_inline(line.strip()))
            continue
        indent = len(m.group(1))
        marker = m.group(2)
        kind = "enumerate" if re.match(r"\d+\.", marker) else "itemize"
        # 关闭更深的栈
        while stack and stack[-1][0] > indent:
            out.append(r"\end{" + stack.pop()[1] + "}")
        if not stack or stack[-1][0] < indent:
            out.append(r"\begin{" + kind + "}")
            stack.append((indent, kind))
        elif stack[-1][1] != kind:
            out.append(r"\end{" + stack.pop()[1] + "}")
            out.append(r"\begin{" + kind + "}")
            stack.append((indent, kind))
        out.append(r"\item " + _inline(m.group(3)))
    while stack:
        out.append(r"\end{" + stack.pop()[1] + "}")
    return "\n".join(out)


PART_RE = re.compile(r"^\s*([-*]\s+)?\*\*(例题|题目|思路|解答|技巧)\*\*\s*[：:]?\s*(.*)$")


def _render_example(block_lines) -> str:
    """按 **题目** 分组，每道题一个独立 examplebox；**例题** 仅作标题来源，不单独开框。
    丢弃没有任何四段内容的空题，避免空紫框。"""
    problems = []   # 每道题 = {"title":..., "parts": {...}}
    cur_prob = None
    cur_part = None
    pending_title = "例题"
    for ln in block_lines:
        m = PART_RE.match(ln)
        if m:
            kind, rest = m.group(2), m.group(3).strip().rstrip("：:").strip()
            if kind == "例题":
                # 例题标记：若有内容则作为下一题标题；本身不开紫框
                pending_title = rest if rest else "例题"
                cur_part = None
            elif kind == "题目":
                # **题目** 才真正开一道新题（一个紫框）
                cur_prob = {"title": pending_title, "parts": {}}
                problems.append(cur_prob)
                cur_part = "题目"
                cur_prob["parts"]["题目"] = [rest] if rest else []
                pending_title = "例题"   # 用完复位
            else:  # 思路/解答/技巧
                if cur_prob is None:
                    cur_prob = {"title": "例题", "parts": {}}
                    problems.append(cur_prob)
                cur_part = kind
                cur_prob["parts"].setdefault(kind, [])
                if rest:
                    cur_prob["parts"][kind].append(rest)
        else:
            # 续行：归入当前 part（解答的多行正文）
            if cur_prob is not None and cur_part is not None and ln.strip():
                cur_prob["parts"].setdefault(cur_part, []).append(ln.strip())
    # 丢弃没有任何四段内容的空题
    problems = [p for p in problems if any(p["parts"].get(k) for k in ("题目", "思路", "解答", "技巧"))]
    if not problems:
        return ""
    out = []
    for prob in problems:
        out.append(r"\begin{examplebox}{" + _inline(prob["title"]) + "}")
        for label in ("题目", "思路", "解答", "技巧"):
            body = prob["parts"].get(label)
            if body:
                body_text = " ".join(b for b in body if b).strip()
                if body_text:
                    out.append(r"\begin{expart}{" + label + "}" + _inline(body_text) + r"\end{expart}")
        out.append(r"\end{examplebox}")
    return "\n".join(out)


def md_to_tex(md: str) -> str:
    # 预处理：去掉纯占位行 '5. 例题' / '4. 例题'（知识点编号残留，无实义）
    md = re.sub(r"(?m)^\s*\d+[.、]\s*例题\s*$", "", md)
    lines = md.splitlines()
    out = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.lstrip()
        # 代码块
        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            j = i + 1
            buf = []
            while j < n and not lines[j].lstrip().startswith("```"):
                buf.append(lines[j]); j += 1
            out.append(_render_codeblock(buf, lang))
            i = j + 1
            continue
        # 例题块：以 **例题/题目/思路/解答/技巧** 起始；容忍四段间的空行
        if PART_RE.match(line):
            block = []
            while i < n:
                l = lines[i]
                if l.lstrip().startswith("#") or l.lstrip().startswith("```"):
                    break
                if PART_RE.match(l):
                    block.append(l); i += 1
                elif l.strip() == "":
                    # 空行：往后看还有无 PART 标记，有则纳入继续
                    k = i + 1
                    while k < n and lines[k].strip() == "":
                        k += 1
                    if k < n and PART_RE.match(lines[k]):
                        block.append(l); i += 1
                    else:
                        break
                else:
                    s = l.strip()
                    # 跳过纯占位行：'5. 例题' / '4. 例题' 这种编号+例题二字、无实质内容
                    if re.match(r"^\d+[.、]\s*例题\s*$", s):
                        i += 1; continue
                    # 例题正文续行（含列表项、解答多步等），全部纳入
                    block.append(l); i += 1
            out.append(_render_example(block))
            continue
        # 标题
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            out.append(_render_heading(len(m.group(1)), m.group(2)))
            i += 1; continue
        # 表格
        if stripped.startswith("|"):
            j = i
            rows = []
            while j < n and lines[j].lstrip().startswith("|"):
                rows.append(lines[j]); j += 1
            out.append(_render_table(rows))
            i = j; continue
        # 列表（容忍项间空行；遇到块级元素才断）
        if _is_list_item(line):
            j = i
            lst = []
            while j < n:
                lj = lines[j]
                if _is_list_item(lj):
                    lst.append(lj); j += 1
                elif lj.strip() == "":
                    # 空行：往后看是否还有列表项，有则跳过空行继续，否则断
                    k = j + 1
                    while k < n and lines[k].strip() == "":
                        k += 1
                    if k < n and _is_list_item(lines[k]):
                        lst.append(lj); j += 1
                    else:
                        break
                elif not lj.lstrip().startswith(("#", "|", "`")) and not PART_RE.match(lj):
                    lst.append(lj); j += 1   # 列表项续行
                else:
                    break
            out.append(_render_list(lst))
            i = j; continue
        # 空行
        if line.strip() == "":
            out.append(""); i += 1; continue
        # 普通段落
        out.append(_inline(line))
        i += 1
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    md = sys.stdin.read() if not sys.argv[1:] else open(sys.argv[1], encoding="utf-8").read()
    print(md_to_tex(md))
