# 复习笔记智能助手（可运行原型）

把一门课程的 PPTX / PDF / DOCX / 图片丢进一个目录，自动输出一份**考试导向**的 Markdown 复习笔记（知识框架 + 各章详解 + 公式/易错点/高频考点汇总 + 速查手册）。

> 设计哲学：**入口无损读取全部资料（绝不截断），出口按考试优先整理（不为覆盖率牺牲复习价值）。**

## 文件结构

| 文件 | 作用 |
|------|------|
| [整理笔记.bat](整理笔记.bat) | **双击即用**的一键入口（调用 run.py） |
| [run.py](run.py) | 一键整理向导（检测 input/ 课件 -> 问课程名 -> 调流水线） |
| [setup.py](setup.py) | **一键环境配置向导**（装依赖 / 选服务商 / 填密钥 / 测连接 / 存 .env） |
| [agent.py](agent.py) | 入口 + 流水线（去重 / 章节 / 分片 / 聚合 / 渲染 / CLI） |
| [config.py](config.py) | `.env` 读写 + 服务商预设 + 启动时自动加载 |
| [extractors.py](extractors.py) | 四种格式逐页提取，带来源溯源；扫描页自动 OCR 兜底 |
| [prompts.py](prompts.py) | 系统提示词（考试导向）+ 分片级 prompt + 学科适配 |
| [llm.py](llm.py) | OpenAI 兼容客户端 + 无 key 时的 Demo 占位后端 |
| [.env.example](.env.example) | 配置模板（手动配置时复制为 .env） |
| [复习笔记助手_设计方案.md](复习笔记助手_设计方案.md) | 完整架构设计文档 |

## 一键使用（最简单，推荐）

第一次使用前先配好 API（只需一次）：

```bash
py setup.py
```

之后每次整理课件，只要两步：

1. **把课件复制进 `input/` 文件夹**（PPT / PDF / DOCX / 图片都行）
2. **双击 `整理笔记.bat`**（或命令行 `py run.py`），输入课程名

向导会自动：检测 `input/` 里的课件 -> 逐页提取 -> 分章整理 -> 输出 **`复习笔记.md`**。

> 命令行等价写法：`py agent.py input -o 复习笔记.md --course 你的课程名`

可选环境变量（写在 `.env` 里可省去每次输入）：
- `REVIEW_COURSE`：默认课程名
- `REVIEW_DISCIPLINE`：默认学科（auto/science/cs/business/arts）
- `REVIEW_MAX_CHARS`：单分片字数上限（默认 6000，长上下文模型可调大以减少分片数）
- `REVIEW_NO_LLM=1`：强制走 Demo 占位输出（离线/测试用）

---

## 安装与环境配置（推荐：一键向导）

最省事的方式——运行交互式配置向导，它会装依赖、让你选服务商填密钥、测连接、并把配置存进 `.env`：

```bash
py setup.py
```

向导流程：
1. 检查 Python 版本
2. 安装依赖（requirements.txt）
3. 选择服务商（智谱 GLM / DeepSeek / 通义千问 / Moonshot / OpenAI / 自定义），输入 API Key（不回显）
4. 实测连接是否成功
5. 保存到 `.env`（已被 .gitignore 忽略，密钥不会上传）

**配好之后，以后只需：**
```bash
py agent.py 你的资料目录 -o 笔记.md --course 课程名
```
不用再带 `--api-base/--api-key/--model`。`--course`/`--discipline` 也可在 `.env` 里设 `REVIEW_COURSE`/`REVIEW_DISCIPLINE` 作为默认值，进一步省略。

> 手动配置：复制 `.env.example` 为 `.env`，填好 `REVIEW_API_BASE`/`REVIEW_API_KEY`/`REVIEW_MODEL` 三项即可。`config.py` 启动时自动加载，真实环境变量优先级高于 `.env`。

图片 OCR 为可选项；需要时再装 `pip install paddleocr paddlepaddle`，否则图片自动跳过并告警。

## 快速体验（无需 API key）

```bash
# 1) 生成一份 tiny 样例资料（pptx + docx）
py agent.py --make-sample sample_data

# 2) Demo 模式跑通整条流水线（输出占位排版，验证流程）
py agent.py sample_data -o 笔记.md --course 宏观经济学 --no-llm
```

## 接真实大模型

系统采用 **OpenAI 兼容接口**，GLM / DeepSeek / 通义千问 / Moonshot / OpenAI 均可直接用。

- 配过 `py setup.py` 后直接运行即可；
- 或临时用命令行参数覆盖（以 GLM 为例）：

```bash
py agent.py sample_data -o 笔记.md \
  --course 宏观经济学 --discipline business \
  --api-base https://open.bigmodel.cn/api/paas/v4/ \
  --api-key 你的KEY \
  --model glm-4-plus
```

也可用环境变量：`REVIEW_API_BASE` / `REVIEW_API_KEY` / `REVIEW_MODEL`。

## 关键参数

| 参数 | 说明 |
|------|------|
| `--course` | 课程名（影响学科自动判定，如"宏观经济学"->经管类） |
| `--discipline` | `auto`/`science`/`cs`/`business`/`arts`/`general`，注入学科专属指令 |
| `--chapters` | 显式章节列表，逗号分隔，强制覆盖自动检测 |
| `--max-chars` | 单分片最大字符数（默认 6000）；资料大时按章节切 + overlap，保证不漏 |

## 它如何保证"读完所有内容、不截断"

1. **提取与推理分离**：全部文字先逐页落成带 `[文件名 P页码]` 的 `Record`，提取阶段没有上下文限制。
2. **按章节分片 + 递归 + overlap**：单章超预算就按记录边界切，相邻片留 1 条重叠，边界概念不丢。
3. **逐片"概念接续"**：把前面已得的概念名喂给后续分片，让模型沿用同名，降低同义碎片。
4. **来源溯源贯穿全程**：每个知识点、每道真题都能点回原始文件页码。

## 输出结构（Markdown）

```
《课程名》复习笔记
- 一、考试范围
- 二、课程知识框架（缩进列表）
- 三、各章详解（核心概念/原理/方法/易错点/题型/本章总结）
- 四、全课程公式汇总
- 五、全课程易错点汇总
- 六、全课程高频考点汇总
- 七、考前速查手册
```

排版严格遵循系统提示词规范：`【定义】` / `【高频】` / `【重点】` / `【必背】` / `【易错点】` / `【真题】` / `【记忆】` / `【解题步骤】` / 公式编号。

## 当前为原型，已知简化

- 去重用"归一哈希 + 子串包含"；完整设计里的 MinHash/语义去重见设计文档。
- 知识合并（实体消解、图合并）在原型里以"逐片拼接 + 重复小标题去重"近似；完整版见设计文档第五阶段。
- 知识网络图用章节树形图表达；完整版的 Mermaid 关系图见设计文档。
