# -*- coding: utf-8 -*-
"""配置管理：从 .env 读取，真实环境变量优先（不被 .env 覆盖）。

不依赖第三方库，手写一个最小 .env 解析器，避免"装包前读不了配置"的鸡生蛋问题。
"""
import os
import sys
from pathlib import Path


def app_root() -> Path:
    """程序数据根目录。

    - 普通运行（CLI：agent.py / run.py / setup.py）：本文件所在目录（项目根）。
    - 打包成 exe（PyInstaller frozen）：exe 所在目录。因为 onefile 模式下 __file__
      指向每次运行都不同的临时解压目录 _MEIxxxx（且只读），input/、.env、work/ 等
      可写数据必须落在 exe 旁边才有意义、才能跨次保留。

    所有可写路径（.env / input / output_notes / work）统一以此为准，避免冻结后
    指向 _MEI 临时目录导致 FileNotFoundError 或配置丢失。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = app_root()
ENV_FILE = ROOT / ".env"
EXAMPLE_FILE = ROOT / ".env.example"

# 常见服务商预设：(显示名, base_url, 默认模型)
PROVIDERS = {
    "1": ("智谱 GLM",       "https://open.bigmodel.cn/api/paas/v4/",           "glm-4-plus"),
    "2": ("DeepSeek",       "https://api.deepseek.com",                         "deepseek-chat"),
    "3": ("通义千问 Qwen",  "https://dashscope.aliyuncs.com/compatible-mode/v1","qwen-plus"),
    "4": ("Moonshot Kimi",  "https://api.moonshot.cn/v1",                        "moonshot-v1-8k"),
    "5": ("OpenAI",         "https://api.openai.com/v1",                         "gpt-4o-mini"),
}


def load_env():
    """读取 .env；已存在的真实环境变量优先（setdefault 不覆盖）。"""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
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
    """把 {KEY: value} 写入 .env；保留注释/空行，覆盖同名键，追加新键。"""
    old_lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    out, seen = [], set()
    for line in old_lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in values:
                out.append(f"{k}={values[k]}")
                seen.add(k)
            else:
                out.append(line)            # 其它键原样保留
        else:
            out.append(line)                # 注释 / 空行原样保留
    for k, v in values.items():
        if k not in seen:
            out.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
