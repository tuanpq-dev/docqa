"""Khởi động docqa: bật Ollama nếu cần, kéo model còn thiếu, chạy server và mở trình duyệt.

    python -m app.launcher [--no-browser] [--no-pull]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from .config import ROOT, settings as s


def say(msg: str):
    print(f"[docqa] {msg}", flush=True)


def get_json(url: str, timeout: float = 3):
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 – chỉ gọi localhost
        return json.loads(r.read().decode("utf-8"))


def ollama_models() -> set[str] | None:
    try:
        data = get_json(s.ollama_url.rstrip("/") + "/api/tags")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    names = set()
    for m in data.get("models", []):
        names.add(m.get("name", ""))
        names.add(m.get("model", ""))
    return names


def has_model(names: set[str], model: str) -> bool:
    return model in names or (":" not in model and model + ":latest" in names)


def ensure_ollama_running() -> bool:
    if ollama_models() is not None:
        return True
    exe = shutil.which("ollama")
    if not exe:
        say("CẢNH BÁO: chưa cài Ollama. Tải tại https://ollama.com/download rồi chạy lại.")
        return False
    say("Ollama chưa chạy – đang khởi động 'ollama serve' ở chế độ nền...")
    log_dir = s.data_path
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / "ollama.log", "ab")  # noqa: SIM115 – giữ mở cho tiến trình con
    kwargs = {"stdout": log, "stderr": log, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([exe, "serve"], **kwargs)  # noqa: S603
    except OSError as e:
        say(f"Không khởi động được Ollama: {e}")
        return False
    for _ in range(40):
        time.sleep(0.5)
        if ollama_models() is not None:
            say("Ollama đã chạy.")
            return True
    say("CẢNH BÁO: Ollama vẫn chưa phản hồi. Hãy mở ứng dụng Ollama thủ công.")
    return False


def ensure_models():
    names = ollama_models() or set()
    for model in (s.embed_model, s.llm_model):
        if has_model(names, model):
            continue
        exe = shutil.which("ollama")
        if not exe:
            say(f"Thiếu model {model}. Hãy chạy: ollama pull {model}")
            continue
        say(f"Đang tải model {model} (chỉ lần đầu, có thể mất vài phút)...")
        if subprocess.call([exe, "pull", model]) != 0:  # noqa: S603
            say(f"CẢNH BÁO: tải model {model} thất bại. Chạy lại sau: ollama pull {model}")


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host if host not in ("0.0.0.0", "") else "127.0.0.1", port)) == 0


def main():
    ap = argparse.ArgumentParser(description="Khởi động docqa")
    ap.add_argument("--no-browser", action="store_true", help="không tự mở trình duyệt")
    ap.add_argument("--no-pull", action="store_true", help="không tự tải model còn thiếu")
    args = ap.parse_args()
    no_browser = args.no_browser or os.environ.get("DOCQA_NO_BROWSER") == "1"

    browse_host = "localhost" if s.host in ("127.0.0.1", "0.0.0.0", "::") else s.host
    url = f"http://{browse_host}:{s.port}"

    if port_in_use(s.host, s.port):
        try:
            get_json(url + "/api/health")
            say(f"docqa đã chạy sẵn tại {url} – mở trình duyệt.")
            if not no_browser:
                webbrowser.open(url)
            return
        except (urllib.error.URLError, OSError, ValueError):
            say(f"LỖI: cổng {s.port} đang bị chương trình khác dùng. Đổi 'port' trong config.yaml hoặc đặt DOCQA_PORT.")
            sys.exit(1)

    if ensure_ollama_running() and not args.no_pull:
        ensure_models()

    def open_when_ready():
        for _ in range(120):
            try:
                get_json(url + "/api/health", timeout=2)
                say(f"Đang mở {url}  (nhấn Ctrl+C để dừng)")
                if not no_browser:
                    webbrowser.open(url)
                return
            except (urllib.error.URLError, OSError, ValueError):
                time.sleep(0.5)

    threading.Thread(target=open_when_ready, daemon=True).start()
    os.chdir(ROOT)
    from .main import main as serve

    serve()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("Đã dừng.")
