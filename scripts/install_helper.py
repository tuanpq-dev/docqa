"""Tiện ích dùng chung cho install.sh / install.ps1 (chạy bằng python trong .venv).

    python scripts/install_helper.py config            # in embed_model, llm_model, ollama_url, port (mỗi dòng một giá trị)
    python scripts/install_helper.py missing           # in các model chưa có; mã thoát 3 nếu Ollama không phản hồi
    python scripts/install_helper.py wait [GIÂY]       # đợi Ollama sẵn sàng; mã thoát 3 nếu hết giờ
    python scripts/install_helper.py pull MODEL        # tải model qua API (dùng khi không có lệnh ollama, vd Ollama ở máy khác)
    python scripts/install_helper.py set-env KEY VALUE # ghi/cập nhật KEY=VALUE trong .env

Cấu hình đọc giống hệt app: config.yaml < .env < biến môi trường DOCQA_*.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import ROOT, settings as s  # noqa: E402
from app.launcher import has_model, ollama_models  # noqa: E402

UNREACHABLE = 3


def cmd_config() -> int:
    print(s.embed_model)
    print(s.llm_model)
    print(s.ollama_url)
    print(s.port)
    return 0


def cmd_missing() -> int:
    names = ollama_models()
    if names is None:
        return UNREACHABLE
    for model in dict.fromkeys((s.embed_model, s.llm_model)):
        if not has_model(names, model):
            print(model)
    return 0


def cmd_wait(seconds: str = "60") -> int:
    deadline = time.monotonic() + float(seconds)
    while time.monotonic() < deadline:
        if ollama_models() is not None:
            return 0
        time.sleep(1)
    return UNREACHABLE


def cmd_pull(model: str) -> int:
    req = urllib.request.Request(
        s.ollama_url.rstrip("/") + "/api/pull",
        data=json.dumps({"model": model, "stream": True}).encode(),
        headers={"Content-Type": "application/json"},
    )
    last = ""
    try:
        with urllib.request.urlopen(req, timeout=600) as r:  # noqa: S310 – URL lấy từ cấu hình của chính người dùng
            for line in r:
                obj = json.loads(line or b"{}")
                if obj.get("error"):
                    print(f"\n[docqa] Ollama báo lỗi: {obj['error']}", file=sys.stderr)
                    return 1
                status = obj.get("status", "")
                if obj.get("total"):
                    status += f" {100 * obj.get('completed', 0) // obj['total']}%"
                if status != last:
                    print(f"\r  {status:<60}", end="", flush=True)
                    last = status
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"\n[docqa] Không tải được {model}: {e}", file=sys.stderr)
        return 1
    print()
    return 0


def cmd_set_env(key: str, value: str) -> int:
    path = ROOT / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    out, done = [], False
    for line in lines:
        if line.strip().split("=", 1)[0].strip() == key:
            if not done:
                out.append(f"{key}={value}")
                done = True
            continue
        out.append(line)
    if not done:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return 0


COMMANDS = {"config": cmd_config, "missing": cmd_missing, "wait": cmd_wait, "pull": cmd_pull, "set-env": cmd_set_env}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    try:
        sys.exit(COMMANDS[sys.argv[1]](*sys.argv[2:]))
    except TypeError:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
