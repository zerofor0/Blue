# -*- coding: utf-8 -*-
"""多文件分阶段处理流水线（5 阶段）。

阶段1 分类 -> 阶段2 课件建骨 -> 阶段3 书/笔记精修 ->
阶段4 试题校准 -> 阶段5 补例题 -> 聚合渲染。
中间产物落盘到 work/，便于检查每一步。
"""
from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import extractors
from extractors import Record
from llm import get_llm_client, CachingClient
from prompts import (
    build_system_prompt, detect_discipline,
    build_classify_prompt, build_courseware_title_prompt, build_courseware_gen_prompt,
    build_courseware_chapter_prompt, build_courseware_outline_prompt,
    build_courseware_ends_prompt,
    build_refine_prompt, build_exam_analyze_prompt, build_exam_merge_prompt,
    build_calibrate_prompt, build_fill_examples_prompt,
)

CHAPTER_RE = re.compile(
    r"^(第\s*[一二三四五六七八九十百零\d]+\s*章|chapter\s*\d+|ch\.?\s*\d+)",
    re.IGNORECASE,
)
EXAMPLE_PLACEHOLDER = "【例题：待补充】"
VALID_TYPES = ("courseware", "book", "note", "exam", "other")

# 整章一次性生成的字符预算：章渲染文本 ≤ 此值则单次调用生成整章（天然单一总览/小结、
# 统一编号、全局一致隶属）；超过则回退到「先大纲后分片」。默认 50000，对 128k 上下文
# 模型安全；小上下文模型可经 REVIEW_CHAPTER_BUDGET 调小。
CHAPTER_SINGLE_CALL_BUDGET = int(os.getenv("REVIEW_CHAPTER_BUDGET", "50000"))


# ============================ 基础件（迁移自 agent.py）============================
@dataclass
class Chapter:
    name: str
    records: list[Record] = field(default_factory=list)


def _normalize(s: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"[，。、；：!?.,;:!?·\-—()（）【】\[\]\"'“”‘’]", "", (s or ""))).lower()


def dedup_records(records: list[Record]) -> list[Record]:
    """两层去重：精确归一哈希 + 子串包含。"""
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
        if len(ni) >= 8 and any(i != j and len(norms[j]) > len(ni) and ni in norms[j] for j in range(len(kept))):
            continue
        final.append(r)
    return final


def _split_text(text: str, max_chars: int) -> list[str]:
    """把超长文本切成每片 <= max_chars，尽量按段落/句子边界，不丢字。"""
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []

    def add(s: str):
        s = s.strip()
        if s:
            pieces.append(s)

    def hard(s: str):
        for i in range(0, len(s), max_chars):
            add(s[i:i + max_chars])

    def by_sentences(line: str):
        sbuf = ""
        for sent in re.findall(r"[^。！？.?!;；]*[。！？.?!;；]?", line):
            if not sent:
                continue
            if len(sent) > max_chars:                       # 单句仍超长 -> 硬切
                if sbuf:
                    add(sbuf); sbuf = ""
                hard(sent)
            elif len(sbuf) + len(sent) > max_chars:
                add(sbuf); sbuf = sent
            else:
                sbuf += sent
        if sbuf:
            add(sbuf)

    buf = ""
    for para in text.split("\n"):
        line = para.strip()
        if not line:
            continue
        seg = line + "\n"
        if len(seg) > max_chars:                            # 单段超长 -> 按句拆
            add(buf); buf = ""
            by_sentences(line)
        elif len(buf) + len(seg) > max_chars:
            add(buf); buf = seg
        else:
            buf += seg
    add(buf)
    return pieces or [text[:max_chars]]


def _split_record(r: Record, max_chars: int) -> list[Record]:
    """单条 Record 超长时，按 _split_text 拆成多条，溯源信息保留。"""
    if len(r.content) <= max_chars:
        return [r]
    out = []
    for i, piece in enumerate(_split_text(r.content, max_chars)):
        out.append(Record(
            source_file=r.source_file, source_type=r.source_type, page=r.page,
            content=piece,
            title=r.title if i == 0 else (f"{r.title} (续{i + 1})" if r.title else ""),
            heading_level=r.heading_level if i == 0 else 0,
        ))
    return out


def chunk_records(records: list[Record], max_chars: int) -> list[list[Record]]:
    # 先把超长单条拆细，保证没有任何单条 > max_chars
    expanded: list[Record] = []
    for r in records:
        expanded.extend(_split_record(r, max_chars))
    records = expanded
    chunks, cur, cur_len = [], [], 0
    for r in records:
        n = len(r.content)
        if cur and cur_len + n > max_chars:
            chunks.append(cur)
            # 仅当上一条较小时保留 overlap，避免大段重复撑爆单片
            cur = [cur[-1]] if len(cur[-1].content) <= max_chars // 4 else []
            cur_len = sum(len(x.content) for x in cur)
        cur.append(r)
        cur_len += n
    if cur:
        chunks.append(cur)
    return chunks or [[]]


def render_chunk_text(records: list[Record]) -> str:
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


def _chapter_heading(index: int, name: str) -> str:
    return f"### {name}" if CHAPTER_RE.match(name) else f"### 第{index}章 {name}"


_CN_NUM = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _chapter_number(name: str) -> int:
    """从章名提取章号用于排序：第7章/第7 章/第七章 -> 7。无章号返回大数（排后）。"""
    m = re.search(r"第\s*([0-9一二三四五六七八九十百零]+)\s*章", name)
    if not m:
        return 10 ** 6
    s = m.group(1)
    if s.isdigit():
        return int(s)
    if "十" in s:                      # 中文数字 1-99：十二/二十三/三十
        a, _, b = s.partition("十")
        tens = _CN_NUM.get(a, 1) if a else 1
        ones = _CN_NUM.get(b, 0) if b else 0
        return tens * 10 + ones
    return _CN_NUM.get(s, 10 ** 6)


def _sort_chapters_by_number(chapters: list[Chapter]) -> list[Chapter]:
    """按章号升序排（稳定排序，无章号者保持原相对顺序排后）。

    修文件名字典序导致的章序错乱：Lecture 07 / Lecture 09 / Lecture08 按字典序排成
    7,9,8（空格 < '0'），但复习笔记应按章号 7,8,9 编排。在 render 前排序，不改变
    各阶段 prompt（仍按文件序处理、命中既有缓存），只调整最终输出与框架树的章序。
    """
    return sorted(chapters, key=lambda c: _chapter_number(c.name))


def _merge_chapter_parts(parts: list[str]) -> str:
    seen_heads = set()
    out_lines = []
    for p in parts:
        for line in p.splitlines():
            s = line.strip()
            if s.startswith("###"):
                # 防御：剥去残留的分片后缀"（本片部分）""（本片）"
                if "本片" in s:
                    line = re.sub(r"（本片部分）|（本片）", "", line)
                    s = re.sub(r"（本片部分）|（本片）", "", s).strip()
                key = _normalize(s)
                if key in seen_heads:
                    continue
                seen_heads.add(key)
            out_lines.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines))
    return text.strip()


def detect_chapters(records: list[Record], explicit: list[str] | None = None) -> list[Chapter]:
    """无课件时用：按 第X章/H1 标题归并 records。"""
    if explicit:
        chapters = {n: Chapter(n) for n in explicit}
        order = explicit
        current = explicit[0]
    else:
        found = []
        for r in records:
            if r.heading_level == 1:
                found.append(r.content.strip())
            elif r.title and CHAPTER_RE.match(r.title.split(" > ")[-1].strip()):
                found.append(r.title.split(" > ")[-1].strip())
            elif CHAPTER_RE.match(r.content.strip()):
                found.append(r.content.strip())
        seen, order = set(), []
        for n in found:
            n2 = re.sub(r"\s+", " ", n).strip()
            if n2 and n2 not in seen:
                seen.add(n2); order.append(n2)
        if not order:
            order = ["全部内容"]
        chapters = {n: Chapter(n) for n in order}
        current = order[0]
    for r in records:
        head = r.title.split(" > ")[-1].strip() if r.title else ""
        txt_head = r.content.strip().split("\n", 1)[0][:40]
        for n in order:
            if n == head or n == txt_head or (CHAPTER_RE.match(n) and n in r.content[:len(n)+4]):
                current = n; break
        chapters[current].records.append(r)
    result = [chapters[n] for n in order if chapters[n].records]
    return result or [Chapter("全部内容", records)]


# ============================ 聚合 / 渲染 ============================
def extract_formulas(md: str) -> list[str]:
    """从各章抽出可放进'公式汇总'的行。只收真正含数学段且数学占主体的公式行，
    剔除反引号整包的代码、中文过多的说明、列表序号文字。"""
    out = []
    for raw in md.splitlines():
        s = re.sub(r"^[-*•]\s+", "", raw.strip())
        # 整行被反引号包住（代码块/误包公式）一律不收
        if s.startswith("`") and s.endswith("`") and s.count("`") == 2:
            continue
        s = s.strip("`* ").strip()
        if not s or s.startswith(("|", "#", "【", "1.", "2.", "3.", "4.", "5.")):
            continue
        # 必须含数学段 $...$ 或 $$...$$
        if "$" not in s:
            continue
        cn_count = len(re.findall(r"[一-龥]", s))
        # 只收以 $ 开头的纯公式行（排除"性质1：$...$"这类带前缀的例题句）
        if not s.startswith("$"):
            continue
        math_len = sum(len(m) for m in re.findall(r"\$\$.*?\$\$|\$[^$]+\$", s, flags=re.S))
        if math_len < len(s) * 0.7:      # 数学段需占主体
            continue
        if cn_count > 2:                 # 含中文即为说明，丢
            continue
        s = re.sub(r"\*+", "", s).strip()
        out.append(s)
    seen, res = set(), []
    for f in out:
        if f not in seen:
            seen.add(f); res.append(f)
    return res


def _collect_block(lines, i):
    j = i + 1
    items = []
    while j < len(lines):
        t = lines[j].strip()
        if not t or t.startswith(("#", "【")) or t.startswith("###"):
            break
        m = re.match(r"^[-*•]\s+(.+)", t) or re.match(r"^\d+[.、]\s*(.+)", t)
        if m:
            items.append(m.group(1).strip()); j += 1
        else:
            break
    return items, j


def extract_easy_wrong(md: str) -> list[str]:
    """识别 【易错点】（含编号项如 '4. 【易错点】：内容'），收集同行内容及后续连续条目。"""
    out, lines, i = [], md.splitlines(), 0
    while i < len(lines):
        s = lines[i].strip()
        # 去掉行首编号 '4. ' 再匹配 【易错点】
        s2 = re.sub(r"^\d+[.、]\s*", "", s)
        m = re.search(r"【\s*易错[^】]*】", s2)
        if m:
            # 同行 【易错点】 后的内容
            rest = s2[m.end():].strip(" ：:-").strip()
            if rest:
                out.append(rest)
            # 后续连续的列表项/续行（直到空行或新标记/标题）
            j = i + 1
            while j < len(lines):
                t = lines[j].strip()
                if not t or t.startswith(("#", "【")) or t.startswith("###") or t.startswith("####"):
                    break
                tm = re.match(r"^[-*•]\s+(.+)", t) or re.match(r"^\d+[.、]\s*(.+)", t)
                if tm:
                    out.append(tm.group(1).strip()); j += 1
                elif "【" in t:
                    break
                else:
                    break
            i = j
        else:
            i += 1
    return out


def extract_high_freq(md: str) -> list[str]:
    """识别高频考点：含 【必背/重点/高频】 标记的行、'**高频考点排序**'、以及带这些标记的 #### 标题。"""
    out, lines, i = [], md.splitlines(), 0
    while i < len(lines):
        s = lines[i].strip()
        # 模式1：【必背/重点/高频】 标记行
        m = re.search(r"【\s*([^】]*?(?:必背|重点|高频)[^】]*?)】", s)
        # 模式2：**高频考点排序**：内容
        m2 = re.match(r"^[-*•]?\s*\*{0,2}高频考点排序\*{0,2}\s*[：:]\s*(.+)", s)
        if m2:
            out.append("【高频排序】" + m2.group(1).strip())
            i += 1; continue
        if m:
            key = m.group(1)
            tag = ("【必背】" if "必背" in key else "【重点】" if "重点" in key else "【高频】")
            # 若是 #### 标题行（如 '#### 概率模型 【必背】'），取标题名
            if s.startswith("####"):
                title = re.sub(r"【[^】]*】", "", s.lstrip("#").strip()).strip()
                if title:
                    out.append(f"{tag} {title}")
            # 同行标记后的内容
            rest = s[m.end():].strip(" ：:-").strip()
            if rest and not s.startswith("####"):
                out.append(f"{tag} {rest}")
            # 后续连续条目
            j = i + 1
            while j < len(lines):
                t = lines[j].strip()
                if not t or t.startswith(("#", "【")) or t.startswith("###"):
                    break
                tm = re.match(r"^[-*•]\s+(.+)", t) or re.match(r"^\d+[.、]\s*(.+)", t)
                if tm:
                    out.append(f"{tag} {tm.group(1).strip()}"); j += 1
                else:
                    break
            i = j
        else:
            i += 1
    # 去重
    seen, res = set(), []
    for x in out:
        if x not in seen:
            seen.add(x); res.append(x)
    return res


def detect_exam_scope(records: list[Record], chapters: list[Chapter]) -> str:
    kws = ["考试范围", "考查范围", "题型", "分值", "闭卷", "开卷", "重点", "占分", "考核"]
    hits = []
    for r in records:
        if any(k in r.content for k in kws):
            for sent in re.split(r"[。\n；]", r.content):
                if any(k in sent for k in kws) and len(sent.strip()) > 4:
                    hits.append(f"- {sent.strip()}（{r.source_file} P{r.page}）")
    if hits:
        return "\n".join(hits[:15])
    return "（资料中未显式标注考试范围，以下为自动识别的章节范围，请以教师最终通知为准）\n" + \
           "\n".join(f"- {ch.name}" for ch in chapters)


def build_framework_tree(chapters: list[Chapter]) -> str:
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


def render_final(course, exam_scope, framework, chapter_md, formulas, easy_wrong,
                 high_freq, chapters, exam_appendix=""):
    parts = [f"# 《{course}》复习笔记\n"]
    parts.append("> 本笔记由 [复习资料整理 Agent] 自动生成：入口无损读取全部原始资料，出口按考试优先原则整理。\n")
    parts.append("## 一、考试范围\n"); parts.append(exam_scope + "\n")
    parts.append("## 二、课程知识框架\n")
    parts.append("```"); parts.append(course); parts.append(framework); parts.append("```\n")
    parts.append("## 三、各章详解\n"); parts.append("\n\n".join(chapter_md) + "\n")
    if exam_appendix:
        parts.append("\n## 附：试题索引与代表题\n")
        parts.append("> 来源于试题资料，按知识点索引（课件题 > 往年真题 > 题库题 > 自编）。\n")
        parts.append(exam_appendix + "\n")
    return "\n".join(parts) + "\n"


# ============================ 工具：JSON / work / 预览 ============================
def _write_work(work: Path, name: str, text: str):
    try:
        (work / name).write_text(text or "", encoding="utf-8")
    except Exception as e:
        print(f"[warn] 写 work/{name} 失败：{e}")


def _parse_json(raw: str):
    if not raw:
        return {}
    raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw.strip())
    raw = re.sub(r"\n?```$", "", raw)
    candidates = [i for i in (raw.find("{"), raw.find("[")) if i >= 0]
    if not candidates:
        return {}
    start = min(candidates)
    try:
        return json.loads(raw[start:])
    except Exception:
        for end_char in ("}", "]"):
            end = raw.rfind(end_char)
            if end > start:
                try:
                    return json.loads(raw[start:end+1])
                except Exception:
                    continue
        return {}


def _preview(recs: list[Record], limit: int = 1000) -> str:
    return "\n".join(r.content for r in recs[:5])[:limit]


# ============================ 阶段1：分类 ============================
def heuristic_classify(fn: str, recs: list[Record]) -> str:
    ext = Path(fn).suffix.lower()
    fn_low = fn.lower()
    blob = fn + " " + " ".join(r.content for r in recs[:5])
    if ext in (".pdf", ".docx", ".doc") and any(k in blob for k in ["试卷", "真题", "题库", "答案", "解析", "试题"]):
        return "exam"
    if ext in (".pptx", ".ppt"):
        return "courseware"
    # 文件名暗示讲义/课件（PDF 导出的课件很常见）
    if any(k in fn_low for k in ["lecture", "handout", "slide", "lec", "课件", "讲义", "幻灯"]):
        return "courseware"
    if any(k in fn for k in ["笔记", "手记", "重点"]) or "note" in fn_low:
        return "note"
    if ext in (".pdf", ".docx", ".doc"):
        return "book"
    return "other"


def classify_files(by_file: dict, client, sys_prompt: str) -> dict:
    previews = [(fn, _preview(recs)) for fn, recs in by_file.items()]
    raw = _safe_chat(client, sys_prompt, build_classify_prompt(previews), "", "文件分类")
    types = _parse_json(raw)
    if not isinstance(types, dict):
        types = {}
    result = {}
    for fn in by_file:
        t = types.get(fn)
        result[fn] = t if t in VALID_TYPES else heuristic_classify(fn, by_file[fn])
    return result


# ============================ 阶段2：课件建骨 ============================
def heuristic_title(recs: list[Record]) -> str | None:
    for r in recs:
        if CHAPTER_RE.match(r.content.strip()):
            return r.content.strip()[:40]
        if r.title and CHAPTER_RE.match(r.title.split(" > ")[-1].strip()):
            return r.title.split(" > ")[-1].strip()[:40]
    for r in recs:
        if r.source_type == "pptx" and r.title:
            return r.title.strip()[:40]
        if r.heading_level == 1:
            return r.content.strip()[:40]
    return None


def _chapter_marker(r: Record) -> str | None:
    c = r.content.strip().split("\n", 1)[0].strip()
    if CHAPTER_RE.match(c):
        return c[:30]
    if r.title:
        last = r.title.split(" > ")[-1].strip()
        if CHAPTER_RE.match(last):
            return last[:30]
    return None


def resolve_titles(cw_by_file: dict, client, sys_prompt: str) -> dict:
    titles, prev, need = {}, None, []
    for fn, recs in cw_by_file.items():
        t = heuristic_title(recs)
        if t:
            titles[fn] = t; prev = t
        else:
            need.append((fn, recs, prev))
    if need:
        info = [(fn, _preview(recs), pv) for fn, recs, pv in need]
        raw = _safe_chat(client, sys_prompt, build_courseware_title_prompt(info), "", "课件标题判定")
        mapping = _parse_json(raw)
        mdict = {}
        if isinstance(mapping, list):
            for item in mapping:
                if isinstance(item, dict) and item.get("file"):
                    mdict[item["file"]] = item
        elif isinstance(mapping, dict):
            for k, v in mapping.items():
                if isinstance(v, dict):
                    mdict[k] = v
        for fn, _recs, pv in need:
            item = mdict.get(fn, {})
            if item.get("is_continuation") and pv:
                titles[fn] = pv
            elif item.get("title"):
                titles[fn] = str(item["title"]); prev = titles[fn]
            else:
                titles[fn] = pv or Path(fn).stem
    return titles


def group_courseware_chapters(cw_by_file: dict, file_titles: dict) -> list[Chapter]:
    chapters, idx = [], {}
    for fn, recs in cw_by_file.items():
        file_title = file_titles.get(fn) or Path(fn).stem
        for r in recs:
            name = _chapter_marker(r) or file_title
            if name not in idx:
                idx[name] = len(chapters)
                chapters.append(Chapter(name))
            chapters[idx[name]].records.append(r)
    if chapters:
        return chapters
    all_recs = [r for recs in cw_by_file.values() for r in recs]
    return [Chapter("课件内容", all_recs)]


def _safe_chat(client, sys_prompt, user, fallback, label):
    """调用模型；连续失败则打印警告并返回 fallback，不抛错、不中断整条流水线。
    失败不会被缓存（CachingClient 只在成功后落盘），故重跑会自动重试本次。"""
    try:
        return client.chat(sys_prompt, user)
    except Exception as e:
        print(f"      [skip] {label} 失败，跳过继续（{type(e).__name__}: {str(e)[:80]}）。重跑会重试。",
              flush=True)
        return fallback


def _track_known(known: list[str], md: str):
    """从已生成章节抽取 ####? 小标题加入 known，供后续章命名一致。"""
    for m in re.findall(r"####?\s*(.+)", md):
        name = m.strip().lstrip("（一）（二）（三）").strip()
        if 2 <= len(name) <= 16:
            known.append(name)


def _split_ends(raw: str) -> tuple[str, str]:
    """把 build_courseware_ends_prompt 的输出拆成 (overview, summary)。

    以单独成行的 [[OVERVIEW]] / [[SUMMARY]] 分段；模型漏标时退化为整体当 summary、overview 留空。
    """
    if not raw:
        return "", ""
    i = raw.find("[[OVERVIEW]]")
    j = raw.find("[[SUMMARY]]")
    if i >= 0 and j > i:
        return raw[i + len("[[OVERVIEW]]"):j].strip(), raw[j + len("[[SUMMARY]]"):].strip()
    if j >= 0:
        return "", raw[j + len("[[SUMMARY]]"):].strip()
    return "", raw.strip()


def _generate_chapter(course, ch, ci, client, sys_prompt, args, work, known):
    """生成单章复习资料。

    整章渲染文本 ≤ CHAPTER_SINGLE_CALL_BUDGET 时单次调用生成整章（天然单一总览/小结、
    统一编号、全局一致隶属）；否则回退到「先大纲后分片」，用大纲统一结构与编号。
    返回该章 Markdown，并把 ####? 小标题加入 known 供后续章命名一致。
    """
    chapter_text = render_chunk_text(ch.records)

    if len(chapter_text) <= CHAPTER_SINGLE_CALL_BUDGET:
        # 单次整章生成
        print(f"      - 第{ci}章 [{ch.name}] 整章一次性生成（{len(chapter_text)} 字）...", flush=True)
        user = build_courseware_chapter_prompt(course, ch.name, chapter_text, known)
        md = _safe_chat(client, sys_prompt, user,
                        f"> （第{ci}章 整章生成失败，待重试）", f"第{ci}章 整章生成")
        _track_known(known, md)
        _write_work(work, f"10_ch{ci}.md", md)
        return md

    # 回退：章过大 -> 先大纲、后分片（按大纲统一结构/编号，避免每片自带总览/小结）
    print(f"      - 第{ci}章 [{ch.name}] 过大（{len(chapter_text)} 字），走大纲引导分片 ...", flush=True)
    skeleton = _safe_chat(client, sys_prompt,
                          build_courseware_outline_prompt(course, ch.name, chapter_text),
                          "", f"第{ci}章 大纲")
    _write_work(work, f"10_ch{ci}_outline.md", skeleton or "")

    chunks = chunk_records(ch.records, args.max_chars)
    parts = []
    for k, ck in enumerate(chunks, 1):
        if not ck:
            continue
        user = build_courseware_gen_prompt(course, ch.name, render_chunk_text(ck), k, len(chunks), known, outline=skeleton)
        print(f"        片 {k}/{len(chunks)} ...", flush=True)
        md = _safe_chat(client, sys_prompt, user,
                        f"> （第{ci}章 片{k} 生成失败，待重试）", f"第{ci}章 片{k}")
        parts.append(md)
        _write_work(work, f"10_ch{ci}_c{k}.md", md)
    body = _merge_chapter_parts(parts)

    # 总览与小结各只一处，在正文装配后统一生成
    overview, summary = _split_ends(_safe_chat(
        client, sys_prompt, build_courseware_ends_prompt(course, ch.name, body),
        "### 本章小结\n- （总览/小结生成失败，待重试）", f"第{ci}章 总览小结"))
    sections = "\n\n".join(p for p in (overview, body, summary) if p and p.strip())
    md = _merge_chapter_parts([sections])
    _track_known(known, md)
    _write_work(work, f"10_ch{ci}.md", md)
    return md


def phase_courseware(courseware_files, by_file, client, sys_prompt, args, work):
    cw_by_file = {fn: by_file[fn] for fn in courseware_files}
    explicit = [s.strip() for s in (args.chapters.split(",") if args.chapters else []) if s.strip()]
    if explicit:
        all_recs = [r for fn in courseware_files for r in by_file[fn]]
        chapters = detect_chapters(all_recs, explicit)
    else:
        file_titles = resolve_titles(cw_by_file, client, sys_prompt)
        _write_work(work, "10_file_titles.json", json.dumps(file_titles, ensure_ascii=False, indent=2))
        chapters = group_courseware_chapters(cw_by_file, file_titles)

    draft, known = {}, []
    for ci, ch in enumerate(chapters, 1):
        draft[ch.name] = _generate_chapter(args.course, ch, ci, client, sys_prompt, args, work, known)
    return chapters, draft


def gen_fallback_chapters(records, client, sys_prompt, args, work, course_label):
    """无课件时：用书/笔记建骨。"""
    chapters = detect_chapters(records)
    draft, known = {}, []
    for ci, ch in enumerate(chapters, 1):
        draft[ch.name] = _generate_chapter(args.course, ch, ci, client, sys_prompt, args, work, known)
    return chapters, draft


# ============================ 阶段3：书/笔记精修 ============================
def _keywords(name: str, draft_md: str) -> set:
    kws = set()
    for tok in (name,):
        for i in range(len(tok) - 1):
            kws.add(tok[i:i+2])
    for h in re.findall(r"^####?\s*(.+)", draft_md, re.M):
        h = h.strip()
        for i in range(len(h) - 1):
            kws.add(h[i:i+2])
    kws.discard("")
    return kws


def match_supplement(ch: Chapter, draft_md: str, recs: list[Record], max_chars: int) -> str:
    kws = _keywords(ch.name, draft_md)
    scored = []
    for r in recs:
        if r.content:
            s = sum(1 for kw in kws if kw in r.content)
            if s > 0:
                scored.append((s, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    chosen, n = [], 0
    for _, r in scored:
        if n + len(r.content) > max_chars:
            break
        chosen.append(r); n += len(r.content)
    if not chosen:
        chosen = recs[:6]
    return render_chunk_text(chosen)


def phase_refine(draft, chapters, supp_recs, kind, client, sys_prompt, args, work):
    if not supp_recs:
        return draft
    for ci, ch in enumerate(chapters, 1):
        supp = match_supplement(ch, draft.get(ch.name, ""), supp_recs, args.max_chars)
        if not supp.strip():
            continue
        print(f"      - 第{ci}章 [{ch.name}] 用{kind}精修 ...", flush=True)
        user = build_refine_prompt(args.course, ch.name, draft[ch.name], supp, kind)
        draft[ch.name] = _safe_chat(client, sys_prompt, user, draft[ch.name], f"第{ci}章 {kind}精修")
        _write_work(work, f"20_ch{ci}.md", draft[ch.name])
    return draft


# ============================ 阶段4：试题校准 ============================
def is_problem_record(r: Record) -> bool:
    return any(k in r.content for k in ["例题", "例 ", "习题", "题目", "计算", "证明", "选择",
                                        "填空", "判断", "简答", "论述", "?", "？", "解：", "答："])


def collect_problems(courseware_recs, exam_recs, exam_label) -> list[tuple[str, Record]]:
    """收集全部候选题目，不做全局截断。tier 决定优先级：课件题 > 往年真题 > 题库题。"""
    probs: list[tuple[str, Record]] = [("课件题", r) for r in courseware_recs if is_problem_record(r)]
    if exam_recs:
        tier = "往年真题" if "真题" in exam_label else "题库题"
        probs += [(tier, r) for r in exam_recs]
    return probs


def match_problems(ch: Chapter, draft_md: str, probs, max_chars: int) -> list[tuple[str, Record]]:
    """按章节关键词相关性匹配题目并截断到预算（相关 + 高优先级在前），而非任意全局截断。"""
    kws = _keywords(ch.name, draft_md)
    tier_w = {"课件题": 4, "往年真题": 3, "题库题": 2}
    scored = []
    for tier, r in probs:
        s = sum(1 for kw in kws if kw in r.content) if r.content else 0
        scored.append((s * 10 + tier_w.get(tier, 0), tier, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    out, n = [], 0
    for _, tier, r in scored:
        if n + len(r.content) > max_chars:
            break
        out.append((tier, r)); n += len(r.content)
    if not out:                                  # 没匹配上：兜底取前几条
        out = probs[:6]
    return out


def render_problem_bank(matched) -> str:
    parts = []
    for tier, r in matched:
        tag = f"[{r.source_file} P{r.page}]" if r.page else f"[{r.source_file}]"
        parts.append(f"({tier}) {tag}\n{r.content}")
    return "\n\n".join(parts) if parts else "（无现成题目来源，请自编合适的例题）"


def phase_exam(draft, chapters, exam_recs, client, sys_prompt, args, work):
    """阶段4：试题分片分析 -> 合并 -> 逐章校准。返回 (draft, analysis)。"""
    chunks = chunk_records(exam_recs, args.max_chars)
    partials = []
    for k, ck in enumerate(chunks, 1):
        if not ck:
            continue
        print(f"      - 分析试题 片 {k}/{len(chunks)} ...", flush=True)
        p = _safe_chat(client, sys_prompt, build_exam_analyze_prompt(args.course, render_chunk_text(ck)),
                       "", f"试题分析 片{k}")
        if p:
            partials.append(p)
    if len(partials) == 1:
        analysis = partials[0]
    elif len(partials) > 1:
        print("      - 合并试题分析 ...", flush=True)
        analysis = _safe_chat(client, sys_prompt, build_exam_merge_prompt(args.course, partials),
                              "\n\n".join(partials), "合并试题分析")   # 合并失败则退化为拼接
    else:
        analysis = ""
    _write_work(work, "30_exam_analysis.md", analysis)
    if not analysis:
        print("      [skip] 试题分析全部失败，跳过逐章校准。", flush=True)
        return draft, analysis
    for ci, ch in enumerate(chapters, 1):
        print(f"      - 第{ci}章 [{ch.name}] 按试题校准 ...", flush=True)
        user = build_calibrate_prompt(args.course, ch.name, draft[ch.name], analysis)
        draft[ch.name] = _safe_chat(client, sys_prompt, user, draft[ch.name], f"第{ci}章 校准")
        _write_work(work, f"30_ch{ci}.md", draft[ch.name])
    return draft, analysis


# ============================ 阶段5：补例题 ============================
def phase_fill_examples(draft, chapters, problems, client, sys_prompt, args, work):
    """problems = [(tier, Record), ...]，按章节相关匹配后喂模型，按 课件>真题>题库>自编 填占位。"""
    for ci, ch in enumerate(chapters, 1):
        if EXAMPLE_PLACEHOLDER not in draft.get(ch.name, ""):
            continue
        if problems:
            matched = match_problems(ch, draft.get(ch.name, ""), problems, args.max_chars)
            bank = render_problem_bank(matched)
        else:
            bank = "（无现成题目来源，请自编合适的例题）"
        print(f"      - 第{ci}章 [{ch.name}] 补例题 ...", flush=True)
        user = build_fill_examples_prompt(args.course, ch.name, draft[ch.name], bank)
        # 失败则保留原草稿（含 【例题：待补充】 占位），不中断；重跑会重试
        draft[ch.name] = _safe_chat(client, sys_prompt, user, draft[ch.name], f"第{ci}章 补例题")
        _write_work(work, f"40_ch{ci}.md", draft[ch.name])
    return draft


# ============================ 主流程 ============================
def run_pipeline(input_dir: Path, out_path: Path, args):
    print(f"[1] 提取文件内容：{input_dir}")
    records = extractors.extract_dir(input_dir)
    if not records:
        print("[error] 未能提取到任何文本，结束。"); return
    print(f"      共提取 {len(records)} 条记录，约 {sum(len(r.content) for r in records)} 字。")
    records = dedup_records(records)

    work = out_path.parent / "work"
    work.mkdir(parents=True, exist_ok=True)

    discipline = args.discipline
    if discipline == "auto":
        discipline = detect_discipline(args.course)
    sys_prompt = build_system_prompt(discipline)
    base_client, model_id = get_llm_client(args)
    use_cache = not getattr(args, "no_cache", False)
    client = CachingClient(base_client, model_id, work / "cache.json", enabled=use_cache)
    if use_cache and client.cache:
        print(f"[resume] 发现 {len(client.cache)} 条缓存，已完成的调用将直接跳过（--no-cache 可强制全重跑）")

    by_file = {}
    for r in records:
        by_file.setdefault(r.source_file, []).append(r)

    # ---- 阶段1 分类 ----
    print("[2] 分类文件 ...")
    file_types = classify_files(by_file, client, sys_prompt)
    _write_work(work, "00_classify.json", json.dumps(file_types, ensure_ascii=False, indent=2))
    for fn, t in file_types.items():
        print(f"      - {fn} -> {t}")

    courseware_files = [fn for fn in by_file if file_types.get(fn) == "courseware"]
    book_recs = [r for fn in by_file if file_types.get(fn) == "book" for r in by_file[fn]]
    note_recs = [r for fn in by_file if file_types.get(fn) == "note" for r in by_file[fn]]
    exam_recs = [r for fn in by_file if file_types.get(fn) == "exam" for r in by_file[fn]]

    # ---- 阶段2 课件建骨 ----
    if courseware_files:
        print(f"[3] 课件建骨：{len(courseware_files)} 个课件文件 ...")
        chapters, draft = phase_courseware(courseware_files, by_file, client, sys_prompt, args, work)
        cw_recs = [r for fn in courseware_files for r in by_file[fn]]
    elif book_recs or note_recs:
        print("[3] 未检测到课件，用书/笔记建骨 ...")
        chapters, draft = gen_fallback_chapters(book_recs + note_recs, client, sys_prompt, args, work, args.course)
        cw_recs = book_recs + note_recs
    else:
        print("[error] 没有可用的课件/书/笔记，结束。"); return

    # ---- 阶段3 书/笔记精修 ----
    if book_recs:
        print("[4] 书籍资料精修 ...")
        draft = phase_refine(draft, chapters, book_recs, "书籍资料", client, sys_prompt, args, work)
    if note_recs:
        print("[4] 笔记精修 ...")
        draft = phase_refine(draft, chapters, note_recs, "笔记", client, sys_prompt, args, work)

    # ---- 阶段4 试题校准 ----
    analysis = ""
    if exam_recs:
        print("[5] 试题分析 + 校准 ...")
        draft, analysis = phase_exam(draft, chapters, exam_recs, client, sys_prompt, args, work)
    exam_label = ("往年真题" if "真题" in analysis else "普通题库") if exam_recs else ""
    problems = collect_problems(cw_recs, exam_recs, exam_label)   # 课件题始终收集；试题有则加入
    _write_work(work, "30_problems.json",
                json.dumps([{"tier": t, "file": r.source_file, "page": r.page} for t, r in problems],
                           ensure_ascii=False, indent=2))

    # ---- 阶段5 补例题 ----
    print("[6] 补例题 ...")
    draft = phase_fill_examples(draft, chapters, problems, client, sys_prompt, args, work)

    # ---- 聚合 + 渲染 ----
    print("[7] 汇总 + 渲染 ...")
    chapters = _sort_chapters_by_number(chapters)   # 按章号排序，修文件名字典序导致的 7,9,8 错乱
    chapter_md = [f"{_chapter_heading(i, ch.name)}\n\n{draft.get(ch.name, '')}"
                  for i, ch in enumerate(chapters, 1)]
    all_md = "\n\n".join(chapter_md)
    leftover = all_md.count(EXAMPLE_PLACEHOLDER)
    if leftover:
        print(f"[warn] 仍有 {leftover} 处 【例题：待补充】 未填（某章补例题失败已跳过）；"
              f"笔记仍已生成，重跑会自动重试这些章节。")
    md = render_final(
        args.course, detect_exam_scope(records, chapters), build_framework_tree(chapters),
        chapter_md, extract_formulas(all_md), [], [], chapters,
        exam_appendix=(analysis if exam_recs else ""),
    )
    out_path.write_text(md, encoding="utf-8")
    if isinstance(client, CachingClient) and (client.hits or client.misses):
        print(f"[cache] 命中 {client.hits} / 共 {client.hits + client.misses} 次调用（仅未命中才真正请求/计费）")
    print(f"[done] 完成 -> {out_path}（中间产物见 {work}）")

    # 可选：生成 PDF（best-effort，失败不影响已产出的 MD）
    if getattr(args, "pdf", False):
        try:
            import build_pdf
            build_pdf.build_pdf(out_path, out_path.with_suffix(".pdf"), args.course)
        except Exception as e:
            print(f"[pdf] PDF 生成失败（不影响 MD）：{type(e).__name__}: {e}")
