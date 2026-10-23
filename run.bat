@echo off
rem docqa - chay bang mot lenh tren Windows: nhap dup run.bat hoac go "run.bat" trong cmd
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

rem 1) Python >= 3.10 (uu tien trinh khoi chay "py")
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo [docqa] Khong tim thay Python 3.10 tro len.
  echo [docqa] Tai tai https://www.python.org/downloads/  ^(nho tick "Add python.exe to PATH"^)
  echo [docqa] hoac chay: winget install Python.Python.3.12
  pause
  exit /b 1
)

rem 2) Ollama
where ollama >nul 2>nul
if errorlevel 1 (
  echo [docqa] Chua cai Ollama ^(can de chay model tren may^).
  echo [docqa] Tai tai https://ollama.com/download  hoac chay: winget install Ollama.Ollama
  echo [docqa] Cai xong hay mo lai cua so nay va chay lai run.bat
  pause
  exit /b 1
)

rem 3) venv + thu vien (chi cai khi requirements.txt thay doi)
if not exist ".venv\Scripts\python.exe" (
  echo [docqa] Tao moi truong ao .venv ...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [docqa] Khong tao duoc venv.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" scripts\ensure_deps.py
if errorlevel 1 (
  pause
  exit /b 1
)

rem 4) Bat Ollama, keo model con thieu, chay server, mo trinh duyet
".venv\Scripts\python.exe" -m app.launcher %*
if errorlevel 1 pause
endlocal