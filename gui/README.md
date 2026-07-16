# 复习笔记助手 · GUI（桌面端）

把命令行工具（`setup.py` 配置 + `run.py`/`agent.py` 整理）做成图形界面桌面程序，可一键打包成 **`.exe`**。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| [gui/复习笔记助手.html](复习笔记助手.html) | GUI 界面（学术静雅风，明暗双模）。双击可在浏览器预览（演示模式）；被后端托管时自动连真实 Python。 |
| [app.py](../app.py) | Flask 后端：枚举 `input/`、读写 `.env`、测试连接、跑流水线，并把 `[1]~[7]` 实时映射成 6 阶段经 SSE 推送。复用 `config.py` / `pipeline.py` / `extractors.py` / `setup.py`。 |
| [launch.py](../launch.py) | 启动器：起 Flask 后台 + PyWebView 原生窗口（Win11 走系统 Edge WebView2）。 |
| [review_notes.spec](../review_notes.spec) | PyInstaller 打包配置（支持 `RN_LITE=1` 去 OCR 精简版）。 |
| [build_exe.bat](../build_exe.bat) | **完整版**打包（含图片 OCR）→ 产出 `复习笔记助手.exe` 到项目根。 |
| [build_lite.bat](../build_lite.bat) | **精简版**打包（去 OCR，体积更小）→ 产出 `复习笔记助手-lite.exe` 到项目根。 |
| [requirements-gui.txt](../requirements-gui.txt) | GUI 额外依赖：flask / pywebview / pyinstaller。 |

## 运行 / 打包

```bash
# 开发调试（原生窗口）
py -m pip install -r requirements.txt
py -m pip install -r requirements-gui.txt
py launch.py

# 打包成 exe（双击对应 .bat 即可，产物在项目根）
build_exe.bat        # 完整版（含 OCR）
build_lite.bat       # 精简版（无 OCR，更小）
```

> 原命令行入口（`整理笔记.bat` / `py run.py` / `py agent.py`）完全不受影响。

## 路径与可移植性（重要）

打包成 exe 后，程序用 **exe 所在目录** 作为数据根（不是解压临时目录 `_MEI`）：

- `input/`（课件）、`output_notes/`（笔记 + PDF）、`work/`（中间产物 + 续跑缓存）、`.env`（API 配置）都建在 **exe 旁边**。
- 所以：把 `复习笔记助手.exe` 连同 `input/`、`.env` 一起拷到任何文件夹都能用（便携）。
- 首次运行：把课件拖进窗口（或放进 `input/`），按向导配好 API 即可。

## exe 里有什么 · 依赖什么 · 能否更小

### exe 内置内容（单文件，完整版约 133 MB）
| 组件 | 大小 | 说明 |
| --- | --- | --- |
| onnxruntime + RapidOCR | ~55 MB | **体积大头**。图片 / 扫描版 PDF 的 OCR 引擎 |
| PyMuPDF (fitz) | ~15-25 MB | PDF 文本提取（必需） |
| openai + httpx | ~12 MB | 大模型调用（必需） |
| pythonnet + pywebview | ~5 MB | 原生窗口（WebView2，必需） |
| python-pptx / python-docx | ~5 MB | PPT / Word 提取（必需） |
| Python 运行时 + flask + 项目代码 | ~20 MB | 其余 |

### 外部依赖（exe 运行时才需要，不打进包内）
- **大模型 API**：必须。在向导里配置（智谱 GLM / DeepSeek / 通义 / Kimi / OpenAI），密钥存 exe 旁的 `.env`。
- **网络**：调用大模型时需要联网。
- **WebView2 运行时**：Win10/11 一般自带（Edge 同源）；极少数精简版系统缺失时打开会白屏，装一下 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) 即可。
- **LaTeX（可选）**：导出 PDF 需要 `xelatex`。向导里可一键装 TinyTeX；不装则只产出 Markdown。

### 缩小体积
1. **用精简版 `build_lite.bat`**：去掉 onnxruntime + RapidOCR（≈ 55 MB），产物约 **75-85 MB**。代价：图片、扫描版 PDF 不再 OCR（文本型 PDF / PPT / Word 提取不受影响）。
2. **装 UPX 压缩**：把 UPX 加入 PATH 后打包，spec 里的 `upx=True` 生效，完整版 / 精简版均可再压缩约 **30%**（如完整版 → ~90 MB）。
3. 其余依赖都是必需的，无明显冗余。

## 工作机制

```
复习笔记助手.exe  (放在项目根 / 任意文件夹)
   └─ launch.py  →  Flask(app.py) 后台  →  pipeline.py（现有流水线）
          ↑ SSE 进度（[1]~[7] → 6 阶段）   ↓ 读写 exe 旁的 input/ output_notes/ work/ .env
        PyWebView 原生窗口渲染  gui/复习笔记助手.html
```

- 同一时刻只允许一个生成任务（后端单任务锁），防止并发抢进度流 / 重复消耗 API。
- 前端按 `location.protocol` 自动切换：`file:` 走 mock（演示），`http:` 走真实接口。打包后恒为真实。
