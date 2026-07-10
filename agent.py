# -*- coding: utf-8 -*-
"""复习笔记智能助手 - 可运行原型（入口）。

用法示例：
    # 1) 生成一份 tiny 样例资料（pptx + docx），无需任何外部素材即可体验
    python agent.py --make-sample sample_data

    # 2) 用真实大模型整理（以 GLM 的 OpenAI 兼容接口为例）
    python agent.py sample_data -o 笔记.md \
        --course "宏观经济学" --discipline business \
        --api-base https://open.bigmodel.cn/api/paas/v4/ \
        --api-key YOUR_KEY --model glm-4-plus

    # 3) 没有key也能跑通整条流水线（Demo 占位输出）
    python agent.py sample_data -o 笔记.md --no-llm
"""

from __future__ import annotations
import argparse
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import config
import extractors
from extractors import Record
from llm import get_llm_client
from prompts import build_system_prompt, build_chunk_user_prompt, detect_discipline

CHAPTER_RE = re.compile(
    r"^(第\s*[一二三四五六七八九十百零\d]+\s*章|chapter\s*\d+|ch\.?\s*\d+)",
    re.IGNORECASE,
)


# ============================ 去重 ============================
def _normalize(s: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"[，。、；：!?.,;:!?·\-—()（）【】\[\]\"'“”‘’]", "", (s or ""))).lower()


def dedup_records(records: list[Record]) -> list[Record]:
    """两层去重：精确归一哈希 + 子串包含（短被长包含则丢短）。"""
    seen, kept = set(), []
    for r in records:
        key = _normalize(r.content)
        if len(key) >= 6 and key in seen:
            continue
        if key:
            seen.add(key)
        kept.append(r)
    norms = [_normalize(r.content) for r in kept]
    final = []
    for i, r in enumerate(kept):
        ni = norms[i]
        if len(ni) >= 8 and any(
            i != j and len(norms[j]) > len(ni) and ni in norms[j]
            for j in range(len(kept))
        ):
            continue
        final.append(r)
    return final


# ============================ 章节构建 ============================
@dataclass
class Chapter:
    name: str
    records: list[Record] = field(default_factory=list)


def detect_chapters(records: list[Record], explicit: list[str] | None = None) -> list[Chapter]:
    """识别章节边界并归并 records。
    优先级：显式列表 > DOCX Heading1 / PPT 中形如"第X章"的标题 > 单章。
    """
    if explicit:
        chapters = {n: Chapter(n) for n in explicit}
        order = explicit
        current = explicit[0]
    else:
        # 收集章节级标题（docx H1 或 标题文本匹配"第X章"）
        found = []
        for r in records:
            if r.heading_level == 1:
                found.append(r.content.strip())
            elif r.title and CHAPTER_RE.match(r.title.split(" > ")[-1].strip()):
                found.append(r.title.split(" > ")[-1].strip())
            elif CHAPTER_RE.match(r.content.strip()):
                found.append(r.content.strip())
        # 保序去重
        seen, order = set(), []
        for n in found:
            n2 = re.sub(r"\s+", " ", n).strip()
            if n2 and n2 not in seen:
                seen.add(n2); order.append(n2)
        if not order:
            order = ["全部内容"]
        chapters = {n: Chapter(n) for n in order}
        current = order[0]

    # 按全局顺序分配：遇到章节标题则切换，否则归入当前章
    for r in records:
        head = ""
        if r.title:
            head = r.title.split(" > ")[-1].strip()
        txt_head = r.content.strip().split("\n", 1)[0][:40]
        switched = False
        for n in order:
            if n == head or n == txt_head or (CHAPTER_RE.match(n) and n in r.content[:len(n)+4]):
                current = n; switched = True; break
        # 章节标题记录本身可并入，但正文不要重复落入下一章
        chapters[current].records.append(r)
    # 过滤掉空章节（除非是唯一章）
    result = [chapters[n] for n in order if chapters[n].records]
    return result or [Chapter("全部内容", records)]


# ============================ 分片 ============================
def chunk_records(records: list[Record], max_chars: int) -> list[list[Record]]:
    """按 record 边界切，相邻片保留 1 条 overlap，保证不漏。"""
    chunks, cur, cur_len = [], [], 0
    for r in records:
        n = len(r.content)
        if cur and cur_len + n > max_chars:
            chunks.append(cur)
            cur = [cur[-1]]  # overlap
            cur_len = len(cur[-1].content)
        cur.append(r)
        cur_len += n
    if cur:
        chunks.append(cur)
    return chunks or [[]]


def render_chunk_text(records: list[Record]) -> str:
    """把 records 渲染成带来源溯源的纯文本喂给模型。"""
    lines = []
    for r in records:
        tag = f"[{r.source_file} P{r.page}]" if r.page else f"[{r.source_file}]"
        if r.title and r.heading_level:
            lines.append(f"{tag}（{r.title}）\n{r.content}")
        elif r.title:
            lines.append(f"{tag} | {r.title}\n{r.content}")
        else:
            lines.append(f"{tag}\n{r.content}")
    return "\n\n".join(lines)


# ============================ 聚合抽取 ============================
def extract_formulas(md: str) -> list[str]:
    """抽公式：带编号 （x-x） 的行优先；否则含 '=' 且像公式的短行。"""
    out = []
    for raw in md.splitlines():
        s = re.sub(r"^[-*•]\s+", "", raw.strip())      # 去列表符
        s = s.strip("`* ").strip()
        if not s or s.startswith(("|", "#", "【")):
            continue
        # 剥离 "必背公式：" 之类的前缀标签
        if "公式" in s and ("：" in s or ":" in s):
            s = re.sub(r"^.*?公式\s*[：:]\s*", "", s).strip("`* ").strip()
            if not s:
                continue
        numbered = bool(re.search(r"（\s*\d+\s*[-－]\s*\d+\s*）|\(\s*\d+\s*-\s*\d+\s*\)", s))
        cn_count = len(re.findall(r"[一-龥]", s))
        is_formula = numbered or (
            "=" in s and len(s) <= 60 and cn_count <= 6
            and re.search(r"[A-Za-z一-鿿]+\s*=\s*", s)
            and not any(w in s for w in ("因为", "所以", "表示", "等于", "定义", "是指"))
        )
        if is_formula:
            out.append(s)
    seen, res = set(), []
    for f in out:
        if f not in seen:
            seen.add(f); res.append(f)
    return res


def _collect_block(lines, i):
    """从标记行 lines[i] 起，收集其后连续的子弹/编号项，返回 (items, next_i)。"""
    j = i + 1
    items = []
    while j < len(lines):
        t = lines[j].strip()
        if not t or t.startswith(("#", "【")) or t.startswith("###"):
            break
        m = re.match(r"^[-*•]\s+(.+)", t) or re.match(r"^\d+[.、]\s*(.+)", t)
        if m:
            items.append(m.group(1).strip())
            j += 1
        else:
            break
    return items, j


def extract_easy_wrong(md: str) -> list[str]:
    out, lines, i = [], md.splitlines(), 0
    while i < len(lines):
        s = lines[i].strip()
        m = re.match(r"【\s*易错[^】]*】", s)
        if m:
            items, nxt = _collect_block(lines, i)
            if items:
                out.extend(items)
            else:
                rest = s[m.end():].strip(" ：:-").strip()
                if rest:
                    out.append(rest)
            i = max(nxt, i + 1)
        else:
            i += 1
    return out


def extract_high_freq(md: str) -> list[str]:
    out, lines, i = [], md.splitlines(), 0
    while i < len(lines):
        s = lines[i].strip()
        m = re.match(r"【\s*([^】]*?)】", s)
        if m and any(k in m.group(1) for k in ("必背", "重点", "高频")):
            tag = ("【必背】" if "必背" in m.group(1)
                   else "【重点】" if "重点" in m.group(1) else "【高频】")
            items, nxt = _collect_block(lines, i)
            if items:
                out.extend(f"{tag} {it}" for it in items)
            else:
                rest = s[m.end():].strip(" ：:-").strip()
                if rest:
                    out.append(f"{tag} {rest}")
            i = max(nxt, i + 1)
        else:
            i += 1
    return out


def detect_exam_scope(records: list[Record], chapters: list[Chapter]) -> str:
    """从资料里抓'考试范围/题型/重点'类提示；找不到则用章节列表兜底。"""
    kws = ["考试范围", "考查范围", "题型", "分值", "闭卷", "开卷", "重点", "占分", "考核"]
    hits = []
    for r in records:
        if any(k in r.content for k in kws):
            # 抽含关键词的句子
            for sent in re.split(r"[。\n；]", r.content):
                if any(k in sent for k in kws) and len(sent.strip()) > 4:
                    hits.append(f"- {sent.strip()}（{r.source_file} P{r.page}）")
    if hits:
        return "\n".join(hits[:15])
    return "（资料中未显式标注考试范围，以下为自动识别的章节范围，请以教师最终通知为准）\n" + \
           "\n".join(f"- {ch.name}" for ch in chapters)


def build_framework_tree(chapters: list[Chapter]) -> str:
    """把章节结构画成缩进列表（替代树形符号，更干净）。"""
    lines = []
    for ch in chapters:
        lines.append(f"  - {ch.name}")
        subs, seen = [], set()
        for r in ch.records:
            if r.heading_level == 2:
                t = r.content.strip()
            elif r.source_type == "pptx" and r.title and not CHAPTER_RE.match(r.title):
                t = r.title.strip()
            else:
                t = ""
            if t and t != ch.name and t not in seen:
                seen.add(t); subs.append(t)
        for s in subs[:12]:
            lines.append(f"      - {s}")
    return "\n".join(lines)


# ============================ 主流程 ============================
def run(input_dir: Path, out_path: Path, args):
    # 1. 提取
    print(f"[1/6] 提取文件内容：{input_dir}")
    records = extractors.extract_dir(input_dir)
    if not records:
        print("[error] 未能从输入目录提取到任何文本，结束。")
        return
    total_chars = sum(len(r.content) for r in records)
    print(f"      共提取 {len(records)} 条记录，约 {total_chars} 字。")

    # 2. 去重
    before = len(records)
    records = dedup_records(records)
    print(f"[2/6] 去重：{before} -> {len(records)} 条。")

    # 3. 章节归并
    explicit = [s for s in (args.chapters.split(",") if args.chapters else []) if s.strip()]
    chapters = detect_chapters(records, explicit or None)
    print(f"[3/6] 识别到 {len(chapters)} 个章节：" + " / ".join(c.name for c in chapters))

    # 4. 分片 + 逐片调模型
    discipline = args.discipline
    if discipline == "auto":
        discipline = detect_discipline(args.course)
    sys_prompt = build_system_prompt(discipline)
    client = get_llm_client(args)

    known_concepts: list[str] = []
    chapter_md: list[str] = []
    print(f"[4/6] 分片整理（max_chars={args.max_chars}）...")
    for ci, ch in enumerate(chapters, 1):
        chunks = chunk_records(ch.records, args.max_chars)
        parts = []
        for k, ck in enumerate(chunks, 1):
            if not ck:
                continue
            user = build_chunk_user_prompt(
                args.course, ch.name, render_chunk_text(ck),
                k, len(chunks), known_concepts,
            )
            print(f"      - 第{ci}章 [{ch.name}] 片 {k}/{len(chunks)} ...", flush=True)
            md = client.chat(sys_prompt, user)
            parts.append(md)
            # 更新已知概念：粗略抽取加粗/【定义】后的名词（取每片前若干候选）
            for m in re.findall(r"####?\s*(.+)", md):
                name = m.strip().lstrip("（一）（二）（三）").strip()
                if 2 <= len(name) <= 16:
                    known_concepts.append(name)
        merged = _merge_chapter_parts(parts)
        chapter_md.append(f"{_chapter_heading(ci, ch.name)}\n\n{merged}")

    # 5. 聚合
    print("[5/6] 汇总公式 / 易错点 / 高频考点 ...")
    all_md = "\n\n".join(chapter_md)
    formulas = extract_formulas(all_md)
    easy_wrong = extract_easy_wrong(all_md)
    high_freq = extract_high_freq(all_md)
    exam_scope = detect_exam_scope(records, chapters)
    framework = build_framework_tree(chapters)

    # 6. 渲染
    print(f"[6/6] 渲染 Markdown -> {out_path}")
    md = render_final(
        args.course, exam_scope, framework, chapter_md,
        formulas, easy_wrong, high_freq, chapters,
    )
    out_path.write_text(md, encoding="utf-8")
    print("[done] 完成。")


def _chapter_heading(index: int, name: str) -> str:
    """章节名已含'第X章'等前缀时不再重复添加。"""
    if CHAPTER_RE.match(name):
        return f"### {name}"
    return f"### 第{index}章 {name}"


def _merge_chapter_parts(parts: list[str]) -> str:
    """合并同一章的多个分片：去掉重复的空行与重复小标题。"""
    seen_heads = set()
    out_lines = []
    for p in parts:
        for line in p.splitlines():
            s = line.strip()
            if s.startswith("###"):
                key = _normalize(s)
                if key in seen_heads:
                    continue
                seen_heads.add(key)
            out_lines.append(line)
    text = "\n".join(out_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def render_final(course, exam_scope, framework, chapter_md,
                 formulas, easy_wrong, high_freq, chapters):
    parts = [f"# 《{course}》复习笔记\n"]
    parts.append("> 本笔记由 [复习资料整理 Agent] 自动生成：入口无损读取全部原始资料，出口按考试优先原则整理。\n")

    parts.append("## 一、考试范围\n")
    parts.append(exam_scope + "\n")

    parts.append("## 二、课程知识框架\n")
    parts.append("```")
    parts.append(course)
    parts.append(framework)
    parts.append("```\n")

    parts.append("## 三、各章详解\n")
    parts.append("\n\n".join(chapter_md) + "\n")

    parts.append("## 四、全课程公式汇总\n")
    parts.append("\n".join(f"- `{f}`" for f in formulas) if formulas else "（详见各章节）")
    parts.append("\n")

    parts.append("## 五、全课程易错点汇总\n")
    parts.append("\n".join(f"- {e}" for e in easy_wrong) if easy_wrong else "（详见各章节【易错点】标记）")
    parts.append("\n")

    parts.append("## 六、全课程高频考点汇总\n")
    parts.append("\n".join(f"- {h}" for h in high_freq) if high_freq else "（详见各章节【必背/重点/高频】标记）")
    parts.append("\n")

    parts.append("## 七、考前速查手册\n")
    parts.append("> 最后冲刺用：每个章节一句话记忆 + 必背公式。\n")
    for ch in chapters:
        parts.append(f"- **{ch.name}**：见该章【记忆】与必背公式。")

    return "\n".join(parts) + "\n"


# ============================ 样例生成 ============================
def make_sample(out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    # PPTX
    try:
        from pptx import Presentation
        prs = Presentation()
        slide_layout = prs.slide_layouts[1]
        s = prs.slides.add_slide(slide_layout)
        s.shapes.title.text = "第1章 国民收入核算"
        s.placeholders[1].text = "GDP 是一定时期内一国境内生产的所有最终产品和服务的市场价值总和。"
        s2 = prs.slides.add_slide(slide_layout)
        s2.shapes.title.text = "1.1 GDP 的核算方法"
        s2.placeholders[1].text = (
            "支出法：GDP = C + I + G + NX。\n"
            "收入法：把各生产要素收入加总。\n"
            "易错：二手交易不计入 GDP。"
        )
        s2.notes_slide.notes_text_frame.text = "教师强调：名义 GDP 与实际 GDP 的区别是高频考点。"
        prs.save(str(out_dir / "宏观经济学.pptx"))
        print(f"[sample] 已生成 {out_dir/'宏观经济学.pptx'}")
    except Exception as e:
        print(f"[sample] 跳过 pptx（{e}）")
    # DOCX
    try:
        from docx import Document
        doc = Document()
        doc.add_heading("第2章 总需求与总供给", level=1)
        doc.add_paragraph("总需求曲线反映价格水平与总需求量的反向关系。")
        doc.add_heading("2.1 总需求曲线的移动", level=2)
        doc.add_paragraph("财政政策与货币政策会使 AD 曲线移动。这是论述题高频考点。")
        doc.add_heading("第3章 IS-LM 模型", level=1)
        doc.add_paragraph("IS 曲线表示产品市场均衡，LM 曲线表示货币市场均衡。")
        doc.save(str(out_dir / "宏观经济学笔记.docx"))
        print(f"[sample] 已生成 {out_dir/'宏观经济学笔记.docx'}")
    except Exception as e:
        print(f"[sample] 跳过 docx（{e}）")
    print(f"[sample] 完成。可运行：python agent.py {out_dir} -o 笔记.md --course 宏观经济学")


# ============================ CLI ============================
def main():
    config.load_env()  # 启动时加载 .env（真实环境变量优先）
    ap = argparse.ArgumentParser(description="复习笔记智能助手（可运行原型）")
    ap.add_argument("input_dir", nargs="?", help="学习资料所在目录")
    ap.add_argument("-o", "--output", default="复习笔记.md", help="输出 Markdown 路径")
    ap.add_argument("--course", default=os.getenv("REVIEW_COURSE", "未命名课程"),
                    help="课程名称（影响学科自动判定；可用 .env 的 REVIEW_COURSE 设默认）")
    ap.add_argument("--discipline", default=os.getenv("REVIEW_DISCIPLINE", "auto"),
                    choices=["auto", "general", "science", "cs", "business", "arts"],
                    help="学科类型，auto 时按课程名推断")
    ap.add_argument("--chapters", default="", help="显式章节列表，逗号分隔，如 '第1章 A,第2章 B'")
    ap.add_argument("--max-chars", type=int, default=6000, help="单分片最大字符数（超则切）")
    ap.add_argument("--api-base", default="", help="OpenAI 兼容接口 base_url")
    ap.add_argument("--api-key", default="", help="API key")
    ap.add_argument("--model", default="", help="模型名")
    ap.add_argument("--no-llm", action="store_true", help="Demo 模式，不调用大模型")
    ap.add_argument("--make-sample", metavar="DIR", help="生成 tiny 样例资料到该目录后退出")
    args = ap.parse_args()

    if args.make_sample:
        make_sample(Path(args.make_sample))
        return
    if not args.input_dir:
        ap.error("请提供输入目录，或用 --make-sample 生成样例。")

    run(Path(args.input_dir), Path(args.output), args)


if __name__ == "__main__":
    main()
