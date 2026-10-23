#!/usr/bin/env bash
# docqa – chạy bằng một lệnh trên macOS / Linux:  ./run.sh
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '[docqa] %s\n' "$*"; }

# 1) Python >= 3.10
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  say "Không tìm thấy Python 3.10 trở lên."
  if [ "$(uname)" = "Darwin" ]; then
    say "Cài bằng: brew install python@3.12   (hoặc tải tại https://www.python.org/downloads/)"
  else
    say "Cài bằng: sudo apt install python3 python3-venv   (Fedora: sudo dnf install python3)"
  fi
  exit 1
fi

# 2) Ollama
if ! command -v ollama >/dev/null 2>&1; then
  say "Chưa cài Ollama (cần để chạy model trên máy)."
  if [ "$(uname)" = "Darwin" ]; then
    say "Cài bằng: brew install ollama   hoặc tải app tại https://ollama.com/download"
  else
    say "Cài bằng: curl -fsSL https://ollama.com/install.sh | sh"
  fi
  say "Cài xong hãy chạy lại ./run.sh"
  exit 1
fi

# 3) venv + thư viện (chỉ cài khi requirements.txt thay đổi)
if [ ! -x .venv/bin/python ]; then
  say "Tạo môi trường ảo .venv ..."
  if ! "$PY" -m venv .venv; then
    say "Không tạo được venv. Trên Debian/Ubuntu hãy cài: sudo apt install python3-venv"
    rm -rf .venv
    exit 1
  fi
fi
.venv/bin/python scripts/ensure_deps.py

# 4) Bật Ollama, kéo model còn thiếu, chạy server, mở trình duyệt
exec .venv/bin/python -m app.launcher "$@"
