@echo off
rem docqa - chay bang mot lenh tren Windows: nhap dup run.bat hoac go "run.bat" trong cmd
rem Chay duoc tu bat ky thu muc nao. Lan dau nen cai bang install.ps1 (xem README, muc "Cai dat nhanh tren Windows").
rem Moi lenh Python deu dung .venv\Scripts\python.exe; Python he thong chi dung de tao .venv lan dau.
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
set "VPY=%~dp0.venv\Scripts\python.exe"

rem 1) .venv - neu chua co thi tao bang Python he thong >= 3.10 (uu tien trinh khoi chay "py")
if exist "%VPY%" goto have_venv
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo [docqa] Chua co .venv va khong tim thay Python 3.10 tro len.
  echo [docqa] Cach de nhat: chay lenh cai trong README ^(install.ps1 tu cai Python, Ollama va model^).
  echo [docqa] Hoac tu cai Python: winget install Python.Python.3.12
  pause
  exit /b 1
)
echo [docqa] Tao moi truong ao .venv ...
%PY% -m venv .venv
if errorlevel 1 (
  echo [docqa] Khong tao duoc .venv.
  pause
  exit /b 1
)
:have_venv

rem 2) Thu vien - chi cai khi requirements.txt thay doi
"%VPY%" scripts\ensure_deps.py
if errorlevel 1 (
  pause
  exit /b 1
)

rem 3) Ollama - kiem tra API theo ollama_url trong config, chua chay thi khoi dong an va doi toi da 30 giay
"%VPY%" scripts\install_helper.py wait 2 >nul 2>nul
if not errorlevel 1 goto ollama_ok
set "OLLAMA_EXE="
for /f "delims=" %%i in ('where ollama 2^>nul') do if not defined OLLAMA_EXE set "OLLAMA_EXE=%%i"
rem PATH chua cap nhat (vd: vua cai xong) -> thu vi tri cai mac dinh
if not defined OLLAMA_EXE if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
if not defined OLLAMA_EXE (
  echo [docqa] Chua cai Ollama ^(can de chay model tren may^).
  echo [docqa] Chay lai lenh cai trong README, hoac: winget install Ollama.Ollama
  echo [docqa] Cai xong hay mo cua so moi va chay lai run.bat
  pause
  exit /b 1
)
echo [docqa] Ollama chua chay - dang khoi dong o che do an...
if not exist data mkdir data
rem "ollama serve" nghe dung dia chi trong config (dong 3 cua "install_helper.py config" la ollama_url)
set "OLLAMA_URL="
for /f "skip=2 delims=" %%u in ('""%VPY%" scripts\install_helper.py config"') do if not defined OLLAMA_URL set "OLLAMA_URL=%%u"
if defined OLLAMA_URL (
  set "OLLAMA_HOST=%OLLAMA_URL:http://=%"
)
if defined OLLAMA_HOST set "OLLAMA_HOST=%OLLAMA_HOST:https://=%"
if defined OLLAMA_HOST if "%OLLAMA_HOST:~-1%"=="/" set "OLLAMA_HOST=%OLLAMA_HOST:~0,-1%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath $env:OLLAMA_EXE -ArgumentList 'serve' -WindowStyle Hidden -RedirectStandardOutput 'data\ollama.out.log' -RedirectStandardError 'data\ollama.log'"
"%VPY%" scripts\install_helper.py wait 30
if errorlevel 1 (
  echo [docqa] CANH BAO: Ollama chua phan hoi sau 30 giay. Xem data\ollama.log, hoac mo Ollama tu Start menu.
) else (
  echo [docqa] Ollama da chay.
)
:ollama_ok

rem 4) Keo model con thieu, chay server, mo trinh duyet
"%VPY%" -m app.launcher %*
if errorlevel 1 pause
endlocal
