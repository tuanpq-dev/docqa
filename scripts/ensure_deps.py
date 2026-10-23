"""Cài requirements vào venv hiện tại chỉ khi requirements.txt thay đổi (lần chạy sau khởi động nhanh).
Chỉ dùng thư viện chuẩn vì lúc chạy có thể chưa cài gì."""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
req = ROOT / "requirements.txt"
stamp = Path(sys.prefix) / ".docqa-requirements.sha256"
digest = hashlib.sha256(req.read_bytes()).hexdigest()

if stamp.is_file() and stamp.read_text().strip() == digest:
    sys.exit(0)

print("[docqa] Đang cài thư viện Python (chỉ lần đầu hoặc khi requirements.txt thay đổi)...", flush=True)
cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(req)]
if subprocess.call(cmd) != 0:
    print("[docqa] LỖI: cài thư viện thất bại. Kiểm tra kết nối mạng rồi chạy lại.", file=sys.stderr)
    sys.exit(1)
stamp.write_text(digest)
print("[docqa] Đã cài xong thư viện.", flush=True)
