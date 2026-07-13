# -*- coding: utf-8 -*-
"""配置管理：从用户主目录的 .env 读取，真实环境变量优先（不被 .env 覆盖）。

密钥默认存在 ~/.review_notes/env（项目目录外），上传项目文件夹不会泄露。
同时保留检查项目目录的 .env（降级兼容旧配置）。
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# 主要：用户主目录下的密钥文件（不在项目内，上传也不泄露）
USER_ENV = Path.home() / ".review_notes" / "env"
# 降级：项目目录的 .env（兼容旧配置）
PROJECT_ENV = ROOT / ".env"
# 兼容旧引用（指向用户目录）
ENV_FILE = USER_ENV
EXAMPLE_FILE = ROOT / ".env.example"

def _find_env() -> Path:
    """返回实际使用的 .env 文件路径（用户主目录优先，项目目录降级）。"""
    if USER_ENV.exists():
        return USER_ENV
    # 项目目录的 .env 只作降级——新建/保存时从来不用它
    return USER_ENV  # 找文件用 USER_ENV；读取时 fallback 到 PROJECT_ENV

# 常见服务商预设：(显示名, base_url, 默认模型)
PROVIDERS = {
    "1": ("智谱 GLM",       "https://open.bigmodel.cn/api/paas/v4/",           "glm-4-plus"),
    "2": ("DeepSeek",       "https://api.deepseek.com",                         "deepseek-chat"),
    "3": ("通义千问 Qwen",  "https://dashscope.aliyuncs.com/compatible-mode/v1","qwen-plus"),
    "4": ("Moonshot Kimi",  "https://api.moonshot.cn/v1",                        "moonshot-v1-8k"),
    "5": ("OpenAI",         "https://api.openai.com/v1",                         "gpt-4o-mini"),
}


def load_env():
    """读取 .env；已存在的真实环境变量优先（setdefault 不覆盖）。
    优先读 ~/.review_notes/env，降级兼容项目目录的 .env。"""
    for path in (USER_ENV, PROJECT_ENV):
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k:
                    os.environ.setdefault(k, v)


def read_env(key: str, default: str = "") -> str:
    load_env()
    return os.environ.get(key, default)


def save_env(values: dict):
    """把 {KEY: value} 写入 ~/.review_notes/env（项目目录外），创建目录。"""
    target = USER_ENV
    target.parent.mkdir(parents=True, exist_ok=True)
    old_lines = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
    out, seen = [], set()
    for line in old_lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in values:
                out.append(f"{k}={values[k]}")
                seen.add(k)
            else:
                out.append(line)
        else:
            out.append(line)
    for k, v in values.items():
        if k not in seen:
            out.append(f"{k}={v}")
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
