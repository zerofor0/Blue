@echo off
REM ============================================================
REM  复习笔记助手 - Build LITE exe (no OCR, smaller ~70-80MB)
REM  与 build_exe.bat 的区别：设 RN_LITE=1，排除 onnxruntime + RapidOCR。
REM  代价：图片 / 扫描版 PDF 不再 OCR（文本型 PDF / PPT / Word 正常）。
REM  Output: dist\<app>-lite.exe → moved to project root.
REM ============================================================
cd /d "%~dp0"
setlocal

set "MIRROR=-i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn"
set "RN_LITE=1"

echo [1/4] Installing runtime deps (Lite skips OCR)...
py -m pip install %MIRROR% -r requirements.txt
if errorlevel 1 ( echo [!] mirror failed, retrying... & py -m pip install -r requirements.txt )

echo.
echo [2/4] Installing GUI/build deps...
py -m pip install %MIRROR% -r requirements-gui.txt
if errorlevel 1 ( echo [!] mirror failed, retrying... & py -m pip install -r requirements-gui.txt )

echo.
echo [3/4] Verifying key packages...
py -c "import flask, webview, PyInstaller" 2>nul
if errorlevel 1 ( echo [X] flask/pywebview/pyinstaller missing. & pause & exit /b 1 )

echo.
echo [4/4] Running PyInstaller (LITE, RN_LITE=1)...
py -m PyInstaller --clean --noconfirm review_notes.spec
if errorlevel 1 ( echo [X] PyInstaller build failed. & pause & exit /b 1 )

echo.
echo Moving exe to project root...
move /Y "%~dp0dist\*.exe" "%~dp0" >nul

echo.
echo ===================== DONE (LITE) =====================
echo exe produced in:  %~dp0
dir /b "%~dp0*-lite.exe" 2>nul
echo ========================================================
echo.
pause
