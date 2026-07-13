# 复习笔记智能助手

把一门课程的 PPTX / PDF / DOCX / 图片丢进一个目录，自动输出一份**考试导向**的复习笔记（Markdown + PDF）。

## 文件结构

| 文件                               | 作用                                              |
| -------------------------------- | ----------------------------------------------- |
| [整理笔记.bat](整理笔记.bat)             | **双击即用**的一键入口（调用 run.py）                        |
| [run.py](run.py)                 | 一键整理向导（检测 input/ 课件 -> 问课程名 -> 调流水线）            |
| [setup.py](setup.py)             | **一键环境配置向导**（装依赖 / 选服务商 / 填密钥 / 测连接 / 存 .env）   |
| [agent.py](agent.py)             | CLI 入口                                          |
| [pipeline.py](pipeline.py)       | 核心流水线（5 阶段：分类 -> 课件建骨 -> 书/笔记精修 -> 试题校准 -> 补例题） |
| [config.py](config.py)           | `.env` 读写 + 服务商预设 + 启动时自动加载                     |
| [extractors.py](extractors.py)   | 四种格式逐页提取，带来源溯源；扫描页自动 OCR 兜底                     |
| [prompts.py](prompts.py)         | 系统提示词（考试导向）+ 各阶段 prompt + 学科适配                  |
| [llm.py](llm.py)                 | OpenAI 兼容客户端（超时/重试/缓存）+ Demo 占位后端               |
| [md2tex.py](md2tex.py)           | Markdown -> LaTeX 转换器（标题/列表/表格/代码/数学/标记/例题彩色盒子） |
| [build_pdf.py](build_pdf.py)     | xelatex 驱动（黄骏斌风格模板）+ xelatex 编译 + 容错            |
| [.env.example](.env.example)     | 配置模板（手动配置时复制为 .env）                             |
| [复习笔记助手_设计方案.md](复习笔记助手_设计方案.md) | 完整架构设计文档                                        |

## 一键使用（最简单，推荐）

第一次使用前先配好 API（只需一次）：

```bash
py setup.py
```

之后每次整理课件，只要两步：

1. **把课件复制进 `input/` 文件夹**（PPT / PDF / DOCX / 图片都行）
2. **双击 `整理笔记.bat`**

双击后会弹出一个黑色命令行窗口，提示你输入课程名（如"概率论"），回车后自动开始：检测 `input/` 里的课件 -> 逐页提取 -> 分类型分阶段整理 -> 输出 **`复习笔记.md`** 和 **`复习笔记.pdf`**（默认自动转 PDF）。跑完窗口显示"完成"，按回车关闭即可。

> 命令行等价写法：`py run.py`（交互式问课程名），或 `py agent.py input -o 复习笔记.md --course 你的课程名`（一步到位）。

可选环境变量（写在 `.env` 里可省去每次输入）：

- `REVIEW_COURSE`：默认课程名
- `REVIEW_DISCIPLINE`：默认学科（auto/science/cs/business/arts）
- `REVIEW_MAX_CHARS`：单分片字数上限（默认 6000，长上下文模型可调大以减少分片数）
- `REVIEW_TIMEOUT`：单次 API 调用超时秒数（默认 300）
- `REVIEW_NO_LLM=1`：强制走 Demo 占位输出（离线/测试用）
- `REVIEW_NO_CACHE=1`：禁用响应缓存（等价 --no-cache）
- `REVIEW_NO_PDF=1`：不转 PDF（等价 --no-pdf）

**中断续跑**：每次调用的响应默认缓存到 `work/cache.json`（按 `模型+提示词` 哈希）。生成被中断后，**直接重跑同一命令即可**——已完成的调用自动命中缓存跳过、不重新计费，只重做中断处那次及之后的。换模型或想全量重跑时加 `--no-cache`。

**PDF 输出（默认自动）**：生成 MD 后自动转 PDF，走 xelatex + tcolorbox，例题用与"微积分A_1__期中复习_黄骏斌.pdf"一致的彩色边框盒子（紫框外盒 + 蓝标题条子盒：题目/思路/解答/技巧）。不想转 PDF 加 `--no-pdf`，或在 `.env` 设 `REVIEW_NO_PDF=1`。需 LaTeX 宏包，首次用 `tlmgr install ctex tcolorbox fancyhdr listings titlesec tabularx hyperref pdfcol mathrsfs amsfonts mathtools bm`（一次性）；没装会自动跳过并提示，不影响 MD 产出。也可单独 `py build_pdf.py 复习笔记.md -o 复习笔记.pdf`。

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

| 参数             | 说明                                                         |
| -------------- | ---------------------------------------------------------- |
| `--course`     | 课程名（影响学科自动判定，如"宏观经济学"->经管类）                                |
| `--discipline` | `auto`/`science`/`cs`/`business`/`arts`/`general`，注入学科专属指令 |
| `--chapters`   | 显式章节列表，逗号分隔，强制覆盖自动检测                                       |
| `--max-chars`  | 单分片最大字符数（默认 6000）；资料大时按章节切 + overlap，保证不漏                  |
| `--no-cache`   | 禁用响应缓存，强制全量重跑（换模型/改提示词后用）；默认开启缓存以支持中断续跑                    |
| `--no-pdf`     | 不生成 PDF；默认自动转 PDF                                          |

## 它如何保证"读完所有内容、不截断"

1. **提取与推理分离**：全部文字先逐页落成带 `[文件名 P页码]` 的 `Record`，提取阶段没有上下文限制。
2. **按章节分片 + 递归 + overlap**：单章超预算就按记录边界切，相邻片留 1 条重叠，边界概念不丢。单条超长记录自动按段/句拆分。
3. **逐片"概念接续"**：把前面已得的概念名喂给后续分片，让模型沿用同名，降低同义碎片。
4. **来源溯源贯穿全程**：每个知识点、每道真题都能点回原始文件页码。

## 输出结构

```
《课程名》复习笔记（.md + .pdf）
- 一、考试范围
- 二、课程知识框架（缩进列表）
- 三、各章详解（章首总览 / 知识点 定义-原理-方法-易错 / 四段式例题 / 章末小结）
- 附：试题索引与代表题（若有试题资料）
```

排版严格遵循系统提示词规范：`【定义】` / `【高频】` / `【重点】` / `【必背】` / `【易错点】` / `【真题】` / `【记忆】` / `【解题步骤】`。例题用四段式（题目/思路/解答/技巧）彩色盒子，PDF 排版仿黄骏斌模板。

## 工作机制（5 阶段流水线）

1. **分类**：自动判别 课件/书/笔记/试题/其他。
2. **课件建骨**：以课件为骨架，按标题分章，逐章生成（例题先留占位）。
3. **书/笔记精修**：对草稿做 增/删/调/换。
4. **试题校准**：分析真题/题库，校准重要性与难度，抽代表题入题库。
5. **补例题**：按 课件题 > 往年真题 > 题库题 > 自编 优先级填占位。

调用失败（超时等）会跳过该处并继续，不中断整条流水线；重跑自动重试失败处。

## 当前为原型，已知简化

- 去重用"归一哈希 + 子串包含"；完整设计里的 MinHash/语义去重见设计文档。
- 知识合并（实体消解、图合并）在原型里以"逐片拼接 + 重复小标题去重"近似；完整版见设计文档第五阶段。
- 知识网络图用章节树形图表达；完整版的 Mermaid 关系图见设计文档。
