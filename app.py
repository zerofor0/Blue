# -*- coding: utf-8 -*-
"""复习笔记助手 · 桌面 GUI 后端（Flask）。

把 GUI（gui/复习笔记助手.html）与现有 Python 流水线（pipeline.py 等）桥接起来：
  - GET  /                 返回 GUI 页面
  - GET  /api/state        环境与配置状态（Python/依赖/LaTeX/.env）
  - GET  /api/files        枚举 input/ 下的课件
  - POST /api/config       保存 .env
  - POST /api/test         测试大模型连接
  - GET  /api/install_deps 校验依赖（打包后依赖随 exe 附带）
  - GET  /api/ensure_latex 非交互安装 TinyTeX（PDF 导出，可选）
  - POST /api/generate     启动流水线，返回 job_id
  - GET  /api/progress/<id>SSE 实时进度（把 [1]~[7] 映射成 6 阶段）
  - GET  /api/outputs      枚举 output_notes/
  - POST /api/open         在资源管理器中打开目录
  - GET  /api/export       下载 md / pdf

由 launch.py 用 PyWebView 打包成原生窗口；也可单独 `python app.py` 在浏览器调试。
"""
from __future__ import annotations
import importlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, Response, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))          # 便于 PyInstaller 打包后仍能 import 同级模块

import config                              # noqa: E402
# pipeline 在 _run_job 内懒加载：保证 GUI 后端即使缺重依赖也能启动，生成时再报错。

app = Flask(__name__, static_folder=None)

# 数据目录锚定到 exe 旁（frozen）或项目根（CLI）；ROOT 仅用于 sys.path 找模块。
DATA_ROOT = config.app_root()
INPUT_DIR = DATA_ROOT / "input"
OUTPUT_DIR = DATA_ROOT / "output_notes"
EXCLUDE_FILE = INPUT_DIR / "_excluded.json"   # GUI「排除」清单：其中的文件不参与生成（不删原文件）
SUPPORTED = {".pptx", ".ppt", ".pdf", ".docx", ".doc",
             ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}

# 流水线打印的 [N] 标记 → GUI 阶段索引（[1]提取+[2]分类 同属阶段0「分类课件」）
MARKER_STAGE = {"1": 0, "2": 0, "3": 1, "4": 2, "5": 3, "6": 4, "7": 5}
NUM_STAGES = 6                                 # GUI 阶段总数（分类/建骨/精修/试题/补例题/渲染）

# 运行中的生成任务：job_id → {"queue": Queue, "thread": Thread}
JOBS: dict[str, dict] = {}
_job_lock = threading.Lock()
CURRENT_JOB = {"id": None}          # 全局唯一：同一时刻只允许一个生成任务在跑


# ============================================================
# 工具
# ============================================================
def _check_deps() -> bool:
    """探测核心依赖是否可 import（打包后恒为 True）。"""
    for mod in ("openai", "pptx", "docx", "fitz"):
        try:
            importlib.import_module(mod)
        except Exception:
            return False
    return True


def _xelatex_available() -> str | None:
    exe = shutil.which("xelatex")
    if exe:
        return exe
    tt = Path(os.environ.get("USERPROFILE", "")) / "AppData/Roaming/TinyTeX/bin/windows/xelatex.exe"
    return str(tt) if tt.exists() else None


def _masked(key: str) -> str:
    return (key[:4] + "***" + key[-2:]) if key and len(key) > 6 else ("***" if key else "")


# ============================================================
# API：状态 / 文件 / 配置 / 测试
# ============================================================
@app.route("/")
def index():
    return send_from_directory(ROOT / "gui", "复习笔记助手.html")


@app.route("/api/state")
def api_state():
    config.load_env()
    base = os.getenv("REVIEW_API_BASE", "")
    key = os.getenv("REVIEW_API_KEY", "")
    model = os.getenv("REVIEW_MODEL", "")
    return jsonify({
        "first_run": not (base and key and model),
        "python_version": "{}.{}.{}".format(*sys.version_info[:3]),
        "python_path": sys.executable,
        "deps_ok": _check_deps(),
        "latex_ok": bool(_xelatex_available()),
        "config": {"base": base, "model": model, "has_key": bool(key), "key_masked": _masked(key)},
    })


def _load_excluded() -> set:
    try:
        return set(json.loads(EXCLUDE_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_excluded(s: set):
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    EXCLUDE_FILE.write_text(json.dumps(sorted(s), ensure_ascii=False), encoding="utf-8")


@app.route("/api/files")
def api_files():
    out = []
    excluded = _load_excluded() if INPUT_DIR.exists() else set()
    present = set()
    if INPUT_DIR.exists():
        for p in sorted(INPUT_DIR.iterdir()):
            if p.is_file() and p.suffix.lower() in SUPPORTED:
                present.add(p.name)
                out.append({
                    "name": p.name,
                    "type": p.suffix.lower().lstrip("."),
                    "size_kb": round(p.stat().st_size / 1024),
                    "excluded": p.name in excluded,
                })
    # 剪掉已不在 input/ 的过期排除项，保持清单整洁
    if excluded - present:
        _save_excluded(excluded & present)
    return jsonify(out)


@app.route("/api/add_file", methods=["POST"])
def api_add_file():
    """前端拖拽/选择文件 → 落到 input/（exe 模式下让拖入的课件真正可被流水线读取）。"""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "message": "未收到文件"}), 400
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    name = Path(f.filename).name                 # 去掉路径，防目录穿越
    f.save(str(INPUT_DIR / name))
    return jsonify({"ok": True, "name": name})


@app.route("/api/exclude", methods=["POST"])
def api_exclude():
    """切换某文件的「排除」状态。排除 = 不参与本次生成，但原文件保留在 input/ 不删除，
    可随时恢复。这样 GUI 的 × 既能真正影响生成，又不会误删课件。"""
    data = request.get_json(silent=True) or {}
    name = Path(data.get("name") or "").name
    exclude = bool(data.get("exclude", True))
    if not name:
        return jsonify({"ok": False, "message": "未指定文件"}), 400
    s = _load_excluded()
    (s.add if exclude else s.discard)(name)
    _save_excluded(s)
    return jsonify({"ok": True, "excluded": name in s})


@app.route("/api/config", methods=["POST"])
def api_config():
    data = request.get_json(silent=True) or {}
    vals = {k: str(v) for k, v in data.items() if k.startswith("REVIEW_") and v is not None}
    config.save_env(vals)
    return jsonify({"ok": True})


@app.route("/api/test", methods=["POST"])
def api_test():
    data = request.get_json(silent=True) or {}
    base, key, model = (data.get("base") or "").strip(), (data.get("key") or "").strip(), (data.get("model") or "").strip()
    if not (base and key and model):
        return jsonify({"ok": False, "message": "配置不完整：base_url / API Key / 模型名 均需填写。", "kind": "incomplete"})
    try:
        from openai import OpenAI
        client = OpenAI(base_url=base, api_key=key)
        t0 = time.time()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "请只回复四个汉字：连接成功"}],
            max_tokens=20, temperature=0,
        )
        reply = resp.choices[0].message.content.strip()
        return jsonify({"ok": True, "reply": reply, "elapsed": round(time.time() - t0, 1)})
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        if any(x in low for x in ("401", "unauthorized", "invalid api key", "authentication")):
            kind = "auth"
        elif "model" in low and ("not" in low or "exist" in low):
            kind = "model"
        elif any(x in low for x in ("connection", "timed out", "resolve", "proxy", "ssl")):
            kind = "net"
        else:
            kind = "other"
        return jsonify({"ok": False, "message": msg[:300], "kind": kind})


@app.route("/api/install_deps")
def api_install_deps():
    # 打包成 exe 后依赖已冻结在内；这里只做一次校验。开发态请用 py setup.py。
    return jsonify({"ok": True, "deps_ok": _check_deps()})


@app.route("/api/ensure_latex")
def api_ensure_latex():
    """非交互安装 TinyTeX（复用 setup.py 的下载/安装逻辑，去掉 input 提示）。"""
    if _xelatex_available():
        return jsonify({"ok": True, "log": "已检测到 xelatex，无需安装。"})
    if os.name != "nt":
        return jsonify({"ok": False, "log": "自动安装仅支持 Windows，请手动安装 MiKTeX/TinyTeX。"})
    installer = ROOT / "install-TinyTeX.ps1"
    url = "https://tinytex.yihui.org/install-bin-windows.ps1"
    try:
        from setup import _download, TLMGR_INSTALL_PKGS, _xelatex_available as _x  # 复用 setup.py
        _download(url, str(installer))
        subprocess.check_call(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer)])
        installer.unlink(missing_ok=True)
        if not _xelatex_available():
            return jsonify({"ok": False, "log": "TinyTeX 似乎未安装成功，可改手动安装 MiKTeX。"})
        bindir = Path(os.environ.get("USERPROFILE", "")) / "AppData/Roaming/TinyTeX/bin/windows"
        if bindir.exists():
            os.environ["PATH"] = str(bindir) + os.pathsep + os.environ.get("PATH", "")
        if shutil.which("tlmgr"):
            subprocess.call(["cmd", "/c", "tlmgr", "install"] + TLMGR_INSTALL_PKGS)
        return jsonify({"ok": True, "log": "TinyTeX 安装完成。"})
    except Exception as e:
        installer.unlink(missing_ok=True)
        return jsonify({"ok": False, "log": f"安装失败：{e}。可手动安装 MiKTeX (https://miktex.org)。"})


# ============================================================
# 生成：后台线程 + stdout 捕获 + SSE
# ============================================================
class JobCapture:
    """把 pipeline 的 stdout 逐行解析成阶段/日志事件，塞进任务队列。"""

    def __init__(self, q: "queue.Queue", mirror):
        self.q = q
        self.mirror = mirror          # 真实 stdout（同步打到控制台，便于调试）
        self.buf = ""
        self.cur = -1                 # 当前 GUI 阶段

    def write(self, s):
        try:
            self.mirror.write(s)
        except Exception:
            pass
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self._handle(line)

    def flush(self):
        try:
            self.mirror.flush()
        except Exception:
            pass

    def _handle(self, line: str):
        sm = re.match(r"^\[(\d)\]", line)              # [1]~[7] 阶段标记
        if sm:
            stage = MARKER_STAGE.get(sm.group(1))
            if stage is not None and stage != self.cur:
                if self.cur >= 0:
                    self.q.put({"type": "stage", "index": self.cur, "status": "done"})
                    # 被跨过的中间阶段（如只有课件时「书/笔记精修」「试题校准」不会运行）标为跳过，
                    # 避免它们一直停在「待开始」显得卡住。
                    for mid in range(self.cur + 1, stage):
                        self.q.put({"type": "stage", "index": mid, "status": "skip"})
                self.cur = stage
                self.q.put({"type": "stage", "index": stage, "status": "run"})
            if self.cur >= 0:
                self.q.put({"type": "log", "index": self.cur, "line": line.strip()})
            return
        if re.match(r"^\[done\]", line):
            if self.cur >= 0:
                self.q.put({"type": "stage", "index": self.cur, "status": "done"})
                # 收尾：尚未触发的阶段（异常早结束等）也标为跳过
                for mid in range(self.cur + 1, NUM_STAGES):
                    self.q.put({"type": "stage", "index": mid, "status": "skip"})
            self.q.put({"type": "done", "meta": {}})
            return
        if re.match(r"^\[error\]", line):
            self.q.put({"type": "error", "message": line.strip()})
            return
        # 普通进度行（逐章/分片/缓存命中 等）挂在当前阶段
        if self.cur >= 0 and line.strip():
            self.q.put({"type": "log", "index": self.cur, "line": line.strip()})


def _run_job(job_id: str, opts: dict, q: "queue.Queue"):
    """在后台线程中跑 pipeline.run_pipeline，stdout 重定向到 JobCapture。"""
    mirror = sys.stdout
    sys.stdout = JobCapture(q, mirror)
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUTPUT_DIR / "复习笔记.md"
        args = SimpleNamespace(
            course=opts.get("course", "未命名课程"),
            discipline=opts.get("discipline", "auto"),
            chapters=opts.get("chapters", ""),
            max_chars=int(opts.get("max_chars", 6000) or 6000),
            api_base="", api_key="", model="",          # 用 .env 里的配置
            no_llm=bool(opts.get("no_llm")),
            no_cache=not opts.get("cache", True),
            pdf=bool(opts.get("pdf", True)),
        )
        import pipeline                              # 懒加载：避免顶层缺依赖即崩
        excluded = _load_excluded()                  # 跳过 GUI 里被「排除」的文件
        pipeline.run_pipeline(INPUT_DIR, out_path, args, exclude=excluded)
        # 若上面没有打印 [done]（理论会打），兜底发一次
        q.put({"type": "done", "meta": {"out": str(out_path)}})
    except Exception as e:
        q.put({"type": "error", "message": f"{type(e).__name__}: {e}"})
    finally:
        sys.stdout = mirror
        with _job_lock:
            if CURRENT_JOB["id"] == job_id:
                CURRENT_JOB["id"] = None
        q.put(None)                                     # 哨兵：通知 SSE 流结束


@app.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.get_json(silent=True) or {}
    with _job_lock:
        # 同一时刻只允许一个生成任务：多任务并发会抢占全局 sys.stdout（进度串流错乱），
        # 真实模式下还会重复消耗 API 额度。已有任务运行时拒绝（HTTP 409）。
        if CURRENT_JOB["id"] and CURRENT_JOB["id"] in JOBS:
            return jsonify({"ok": False, "error": "busy",
                            "message": "已有生成任务在运行，请等它完成后再试。"}), 409
        job_id = uuid.uuid4().hex[:12]
        q: "queue.Queue" = queue.Queue()
        CURRENT_JOB["id"] = job_id
        t = threading.Thread(target=_run_job, args=(job_id, data, q), daemon=True)
        JOBS[job_id] = {"queue": q, "thread": t}
        t.start()
    return jsonify({"job_id": job_id})


@app.route("/api/progress/<job_id>")
def api_progress(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "no such job"}), 404
    q = job["queue"]

    def stream():
        while True:
            try:
                evt = q.get(timeout=20)
            except queue.Empty:
                yield ": ping\n\n"                      # 保活
                continue
            if evt is None:
                break
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            if evt.get("type") in ("done", "error"):
                # 给客户端一点时间收尾后清理
                yield ": end\n\n"
                break
        JOBS.pop(job_id, None)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ============================================================
# 产物
# ============================================================
@app.route("/api/outputs")
def api_outputs():
    out = []
    if OUTPUT_DIR.exists():
        for p in sorted(OUTPUT_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file() and p.suffix.lower() in {".md", ".pdf"}:
                out.append({"name": p.name, "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))})
    return jsonify(out)


@app.route("/api/open", methods=["POST"])
def api_open():
    data = request.get_json(silent=True) or {}
    # 用 DATA_ROOT（exe 旁/项目根），不能用 ROOT——冻结后 ROOT 指向临时 _MEI 目录
    target = DATA_ROOT / (data.get("path") or "output_notes")
    target.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt":
            os.startfile(str(target))                  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)})


@app.route("/api/export")
def api_export():
    kind = (request.args.get("kind") or "md").lower()
    name = "复习笔记.pdf" if kind == "pdf" else "复习笔记.md"
    p = OUTPUT_DIR / name
    if not p.exists():
        return jsonify({"ok": False, "message": f"未找到 {name}，请先生成。"}), 404
    return send_from_directory(OUTPUT_DIR, name, as_attachment=True)


# ============================================================
def find_free_port(default=51200):
    import socket
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", default))
        return default
    except OSError:
        s.close()
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port


PORT = None


if __name__ == "__main__":
    PORT = find_free_port()
    print(f"[gui] 复习笔记助手后台运行于 http://127.0.0.1:{PORT}/ （按 Ctrl+C 退出）")
    app.run(host="127.0.0.1", port=PORT, threaded=True, debug=False)
