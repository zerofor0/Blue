@echo off
REM ============================================================
REM  复习笔记助手 - Build to exe (Windows)
REM  Output: dist\<app>.exe  (single file)
REM
REM  说明 / Notes:
REM   - 本文件刻意只用 ASCII，避免在中文 Windows（GBK 终端）下因
REM     chcp/多字节字符解析出错。exe 的中文名由 review_notes.spec 提供。
REM   - 默认走清华镜像，失败自动回退官方源；离线/已装时会很快跳过。
REM   - 调试打包：把 review_notes.spec 里 console=False 改 True 可看后台日志。
REM ============================================================
cd /d "%~dp0"
setlocal

set "MIRROR=-i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn"

echo [1/4] Installing runtime deps (Tsinghua mirror)...
py -m pip install %MIRROR% -r requirements.txt
if errorlevel 1 (
    echo [!] mirror failed, retrying with default index...
    py -m pip install -r requirements.txt
)

echo.
echo [2/4] Installing GUI/build deps (flask / pywebview / pyinstaller)...
py -m pip install %MIRROR% -r requirements-gui.txt
if errorlevel 1 (
    echo [!] mirror failed, retrying with default index...
    py -m pip install -r requirements-gui.txt
)

echo.
echo [3/4] Verifying key packages are importable...
py -c "import flask, webview, PyInstaller" 2>nul
if errorlevel 1 (
    echo [X] flask / pywebview / pyinstaller not installed. Fix above errors first.
    pause
    exit /b 1
)

echo.
echo [4/4] Running PyInstaller (first build takes a few minutes)...
py -m PyInstaller --clean --noconfirm review_notes.spec
if errorlevel 1 (
    echo [X] PyInstaller build failed.
    pause
    exit /b 1
)

echo.
echo Moving exe to project root (next to build_exe.bat)...
move /Y "%~dp0dist\*.exe" "%~dp0" >nul
if not exist "%~dp0*.exe" (
    echo [X] exe not found after move. Check dist\ folder.
    pause
    exit /b 1
)

echo.
echo ===================== DONE =====================
echo exe produced in:  %~dp0
dir /b "%~dp0*.exe"
echo ================================================
echo.
pause
