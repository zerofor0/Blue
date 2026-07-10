# -*- coding: utf-8 -*-
"""大模型客户端抽象。

支持两种后端：
1. OpenAI 兼容接口（GLM / DeepSeek / 通义千问 / Moonshot / OpenAI 等均兼容）：
   通过 --api-base / --api-key / --model 或环境变量配置。
2. Demo 模式（--no-llm）：不调用任何 API，返回按规范排版的示例片段，
   便于在没有 key 时立即验证提取 -> 分片 -> 装配整条流水线。
"""

from __future__ import annotations
import os


class LLMClient:
    """OpenAI 兼容客户端。"""
    def __init__(self, base_url, api_key, model):
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError("未安装 openai 包，请 `pip install openai` 或使用 --no-llm") from e
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()


class DemoClient:
    """无 API key 时的占位后端：返回符合排版规范的示例片段，验证流水线。"""
    def chat(self, system_prompt: str, user_prompt: str) -> str:
        # 从 user prompt 里抓当前章节名，便于演示
        chapter = "本章"
        for line in user_prompt.splitlines():
            if line.startswith("当前章节："):
                chapter = line.replace("当前章节：", "").strip()
                break
        return f"""#### （示例）核心知识点

【定义】这是 Demo 模式生成的占位定义。配置真实 API 后，此处将是大模型基于原始资料整理的精确定义。

**核心原理**：因果关系 / 推导逻辑 / 理论依据（Demo 占位）。

【重点】
- 考点一：该知识点的典型考察方式（Demo 占位）。
- 考点二：常结合 XX 一起出题（Demo 占位）。

【解题步骤】
1. 第一步...
2. 第二步...
3. 应用场景：...

【易错点】
- 容易混淆：A 与 B 的区别。
- 常见错误：把 X 当成 Y。

【真题】（模拟题）试说明该概念的关键性质。（来源：见原始资料）

#### 本章总结
- 本章核心结论：（Demo 占位）
- 【记忆】一句话记忆：原因 -> 过程 -> 结果。

**必背公式**

F = m a  （1-1）
"""


def get_llm_client(args) -> object:
    if args.no_llm:
        print("[info] 使用 Demo 模式（不调用大模型），输出为占位示例。")
        return DemoClient()
    base = args.api_base or os.getenv("REVIEW_API_BASE")
    key = args.api_key or os.getenv("REVIEW_API_KEY")
    model = args.model or os.getenv("REVIEW_MODEL")
    if not (base and key and model):
        print("[info] 未配置 API，自动回退到 Demo 模式。"
              "（可用 --api-base/--api-key/--model 或环境变量 REVIEW_API_BASE/REVIEW_API_KEY/REVIEW_MODEL）")
        return DemoClient()
    print(f"[info] 使用大模型：{model} @ {base}")
    return LLMClient(base, key, model)
