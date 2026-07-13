# -*- coding: utf-8 -*-
"""大模型客户端抽象。

支持两种后端：
1. OpenAI 兼容接口（GLM / DeepSeek / 通义千问 / Moonshot / OpenAI 等均兼容）：
   通过 --api-base / --api-key / --model 或环境变量配置。
2. Demo 模式（--no-llm）：不调用任何 API，返回按规范排版的示例片段，
   便于在没有 key 时立即验证提取 -> 分片 -> 装配整条流水线。
"""

from __future__ import annotations
import hashlib
import json
import os
import re
from pathlib import Path

_PLACEHOLDER = "【例题：待补充】"


def _extract_section(text: str, header: str) -> str:
    """从 user prompt 里抽取 === header === 下方、下一个 === ... === 之前的内容。"""
    marker = f"=== {header} ==="
    i = text.find(marker)
    if i < 0:
        return ""
    rest = text[i + len(marker):].lstrip("\n")
    j = re.search(r"\n=== .+ ===", rest)
    return rest[:j.start()].strip() if j else rest.strip()


def _demo_classify(fn: str) -> str:
    f = fn.lower()
    if "题" in fn or "试卷" in fn or "真题" in fn or "exam" in f:
        return "exam"
    if f.endswith(".pptx") or f.endswith(".ppt"):
        return "courseware"
    if any(k in f for k in ["lecture", "handout", "slide", "lec", "课件", "讲义"]):
        return "courseware"
    if "笔记" in fn or "重点" in fn or "note" in f:
        return "note"
    return "book"


def _demo_chapter() -> str:
    """cw-gen 阶段占位输出，展示模板式结构（含 【例题：待补充】，留给阶段5 填）。"""
    return (
        "**本章总览**（Demo 占位）：核心问题、高频考点排序、方法清单。配置真实 API 后为基于课件的总览。\n\n"
        "#### （示例）核心知识点 【重点】\n\n"
        "【定义】Demo 占位定义。配置真实 API 后为基于课件整理的精确定义。\n\n"
        "**原理与推导**：因果关系 / 推导过程 / 理论依据（Demo 占位）。\n\n"
        "【解题步骤】\n1. 第一步...\n2. 第二步...\n3. 应用场景：...\n\n"
        "【易错点】\n- 容易混淆：A 与 B 的区别。\n- 常见错误：把 X 当成 Y。\n\n"
        "**例题**\n" + _PLACEHOLDER + "\n\n"
        "#### 本章小结\n"
        "- 知识关系：（Demo 占位）\n"
        "- 核心结论：（Demo 占位）\n"
        "- 必背公式：`F = m a  （1-1）`\n"
        "- 【记忆】原因 -> 过程 -> 结果。\n"
    )


class LLMClient:
    """OpenAI 兼容客户端。"""
    def __init__(self, base_url, api_key, model, timeout=240):
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError("未安装 openai 包，请 `pip install openai` 或使用 --no-llm") from e
        # max_retries=0：自己在 chat() 里重试，便于打印进度，避免 SDK 默认重试导致的长时间沉默
        self.client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0)
        self.model = model
        self.timeout = timeout

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        import time
        last_err = None
        for attempt, delay in enumerate([0, 15, 30], 1):   # 共 3 次，逐次退避
            if delay:
                print(f"      [retry] 第 {attempt} 次尝试（{delay}s 后）...", flush=True)
                time.sleep(delay)
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.3,
                    timeout=self.timeout,   # 单次超时，默认 240s，可用 REVIEW_TIMEOUT 调
                )
                return resp.choices[0].message.content.strip()
            except Exception as e:
                last_err = e
                print(f"      [warn] 调用失败（{type(e).__name__}）：{str(e)[:150]}", flush=True)
        raise RuntimeError(f"大模型连续 3 次调用失败：{last_err}")


class DemoClient:
    """无 API key 时的占位后端：按 prompt 里的 <!-- phase: NAME --> 返回对应阶段的占位输出。"""
    def chat(self, system_prompt: str, user_prompt: str) -> str:
        m = re.search(r"phase:\s*([\w-]+)", user_prompt)
        phase = m.group(1) if m else "cw-gen"

        if phase == "classify":
            files = re.findall(r"### 文件：(.+)", user_prompt)
            return json.dumps({fn.strip(): _demo_classify(fn) for fn in files}, ensure_ascii=False)

        if phase == "cw-title":
            files = re.findall(r"### 文件：(.+)", user_prompt)
            arr = [{"file": fn.strip(), "title": Path(fn.strip()).stem, "is_continuation": False} for fn in files]
            return json.dumps(arr, ensure_ascii=False)

        if phase == "refine":
            draft = _extract_section(user_prompt, "当前复习资料")
            return (draft or _demo_chapter()) + "\n\n> （Demo：已按书籍/笔记做增删调换，占位未动）"

        if phase == "exam-analyze":
            return (
                "## 考试分析（Demo 占位）\n\n"
                "1. 试题类型：往年真题（Demo 判定）。\n"
                "2. 高频考点：均衡价格、需求价格弹性、需求曲线移动。\n"
                "3. 难点：弹性的计算与分类。\n"
                "4. 易得分点：需求/供给定律的方向。\n"
                "5. 考试模式：选择 + 计算，计算题占分高。\n"
                "6. 代表题：均衡求解、弹性计算（见题目来源池）。\n"
            )

        if phase == "exam-merge":
            return (
                "## 考试分析（Demo 合并占位）\n\n"
                "1. 试题类型：往年真题。\n"
                "2. 高频考点：均衡价格、需求价格弹性。\n"
                "3. 难点：弹性计算。\n"
                "4. 易得分点：需求/供给定律方向。\n"
                "5. 考试模式：选择 + 计算。\n"
                "6. 代表题：均衡求解、弹性计算。\n"
            )

        if phase == "calibrate":
            draft = _extract_section(user_prompt, "本章草稿")
            return (draft or _demo_chapter()) + "\n\n> （Demo：已按考试分析校准重要性，高频/难点前置）"

        if phase == "fill-examples":
            draft = _extract_section(user_prompt, "本章草稿") or _demo_chapter()
            return draft.replace(
                _PLACEHOLDER,
                "- **题目**：【真题】（Demo）已知 Qd=100-2P，Qs=20+2P，求均衡价格与数量。\n"
                "- **思路**：均衡即令需求等于供给，解关于 P 的方程。\n"
                "- **解答**：100-2P = 20+2P -> 4P = 80 -> P=20；Q = 100-2*20 = 60。\n"
                "- **技巧**：求均衡统一'令 Qd=Qs'，注意定义域。",
            )

        # 默认：cw-gen（课件建骨分片）
        return _demo_chapter()


class CachingClient:
    """包装底层 client，按 (model_id, system, user) 的哈希缓存响应，支持中断续跑。

    每次 miss 后立即落盘，所以任何时候中断，已完成的调用下次都能命中跳过。
    """

    def __init__(self, base, model_id, cache_path: Path, enabled: bool = True):
        self.base = base
        self.model_id = model_id
        self.cache_path = cache_path
        self.enabled = enabled
        self.cache: dict = {}
        self.hits = 0
        self.misses = 0
        if enabled:
            self._load()

    def _load(self):
        if self.cache_path.exists():
            try:
                self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception:
                self.cache = {}

    def _persist(self):
        try:
            self.cache_path.write_text(
                json.dumps(self.cache, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[warn] 写缓存失败：{e}")

    def _key(self, system: str, user: str) -> str:
        h = hashlib.sha256()
        h.update(self.model_id.encode("utf-8")); h.update(b"\x00")
        h.update((system or "").encode("utf-8")); h.update(b"\x00")
        h.update((user or "").encode("utf-8"))
        return h.hexdigest()

    def chat(self, system: str, user: str) -> str:
        key = None
        if self.enabled:
            key = self._key(system, user)
            if key in self.cache:
                self.hits += 1
                return self.cache[key]
        resp = self.base.chat(system, user)
        self.misses += 1
        if self.enabled and key is not None:
            self.cache[key] = resp
            self._persist()   # 每次未命中后立刻存盘，保证中断安全
        return resp


def get_llm_client(args):
    """返回 (client, model_id)。model_id 用于缓存键（'demo' 或真实模型名）。"""
    if args.no_llm:
        print("[info] 使用 Demo 模式（不调用大模型），输出为占位示例。")
        return DemoClient(), "demo"
    base = args.api_base or os.getenv("REVIEW_API_BASE")
    key = args.api_key or os.getenv("REVIEW_API_KEY")
    model = args.model or os.getenv("REVIEW_MODEL")
    if not (base and key and model):
        print("[info] 未配置 API，自动回退到 Demo 模式。"
              "（可用 --api-base/--api-key/--model 或环境变量 REVIEW_API_BASE/REVIEW_API_KEY/REVIEW_MODEL）")
        return DemoClient(), "demo"
    try:
        timeout = int(os.getenv("REVIEW_TIMEOUT", "300"))
    except ValueError:
        timeout = 300
    print(f"[info] 使用大模型：{model} @ {base}（单次超时 {timeout}s）")
    return LLMClient(base, key, model, timeout), model
