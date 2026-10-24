"""Tiện ích dùng chung cho install.sh / install.ps1 (chạy bằng python trong .venv).

    python scripts/install_helper.py config            # in embed_model, llm_model, ollama_url, port (mỗi dòng một giá trị)
    python scripts/install_helper.py missing           # in các model chưa có; mã thoát 3 nếu Ollama không phản hồi
    python scripts/install_helper.py wait [GIÂY]       # đợi Ollama sẵn sàng; mã thoát 3 nếu hết giờ
    python scripts/install_helper.py pull MODEL        # tải model qua API (dùng khi không có lệnh ollama, vd Ollama ở máy khác)
    python scripts/install_helper.py set-env KEY VALUE # ghi/cập nhật KEY=VALUE trong .env
    python scripts/install_helper.py auto-llm [--dry-run]  # máy RAM < 12 GB: dùng qwen2.5:3b thay qwen3.5:4b

Cấu hình đọc giống hệt app: config.yaml < .env < biến môi trường DOCQA_*.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import ROOT, settings as s  # noqa: E402
from app.launcher import has_model, ollama_models  # noqa: E402

UNREACHABLE = 3
SWITCHED = 10


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


# ---------- chọn model theo RAM ----------
DEFAULT_LLM = "qwen3.5:4b"   # model mặc định trong config.yaml
LIGHT_LLM = "qwen2.5:3b"     # nhẹ hơn ~1,4 GB RAM khi chạy
MIN_RAM_GB = 12              # máy "8 GB" báo ~7,8 GB, máy "16 GB" báo ~15,7 GB


def total_ram_gb() -> float | None:
    """Tổng RAM vật lý (GB), chỉ dùng thư viện chuẩn; None nếu không đọc được.
    DOCQA_RAM_GB ghi đè giá trị đo được (để thử logic chọn model trên máy khác cấu hình)."""
    if os.environ.get("DOCQA_RAM_GB"):
        try:
            return float(os.environ["DOCQA_RAM_GB"])
        except ValueError:
            pass
    try:
        if sys.platform == "win32":
            import ctypes

            class MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = MemStatus()
            st.dwLength = ctypes.sizeof(MemStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return None
            return st.ullTotalPhys / 1024**3
        if sys.platform == "darwin":
            import subprocess

            return int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)) / 1024**3
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024**2
    except (OSError, ValueError, AttributeError):
        return None
    return None


def cmd_auto_llm(*flags: str) -> int:
    """Máy ít RAM thì ghi DOCQA_LLM_MODEL=<model nhẹ> vào .env – chỉ khi người dùng chưa tự chọn model.
    --dry-run: chỉ in ra, không ghi. Mã thoát SWITCHED (10) khi chọn model nhẹ, để script cài biết."""
    import yaml

    dry = "--dry-run" in flags
    ram = total_ram_gb()
    if ram is None:
        print("Khong doc duoc dung luong RAM - giu model theo config.yaml")
        return 0
    env_file = _parse_env(ROOT / ".env")
    cfg_path = ROOT / "config.yaml"
    cfg = (yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}) if cfg_path.is_file() else {}
    if "DOCQA_LLM_MODEL" in env_file:
        print(f"RAM {ram:.1f} GB; .env da chon model {env_file['DOCQA_LLM_MODEL']} - giu nguyen")
        return 0
    if cfg.get("llm_model", DEFAULT_LLM) != DEFAULT_LLM:
        print(f"RAM {ram:.1f} GB; config.yaml da chon model {cfg['llm_model']} - giu nguyen")
        return 0
    if ram >= MIN_RAM_GB:
        print(f"RAM {ram:.1f} GB - du cho {DEFAULT_LLM}")
        return 0
    print(f"RAM {ram:.1f} GB < {MIN_RAM_GB} GB - dung model nhe hon {LIGHT_LLM} thay cho {DEFAULT_LLM}"
          + (" (dry-run: chua ghi .env)" if dry else " (ghi vao .env; muon doi lai thi xoa dong DOCQA_LLM_MODEL)"))
    if not dry:
        cmd_set_env("DOCQA_LLM_MODEL", LIGHT_LLM)
    return SWITCHED


def _parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


COMMANDS = {"config": cmd_config, "missing": cmd_missing, "wait": cmd_wait, "pull": cmd_pull, "set-env": cmd_set_env,
            "auto-llm": cmd_auto_llm}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    try:
        sys.exit(COMMANDS[sys.argv[1]](*sys.argv[2:]))
    except TypeError:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
