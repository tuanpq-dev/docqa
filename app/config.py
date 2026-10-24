"""Đọc cấu hình từ config.yaml, .env và biến môi trường DOCQA_*."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    ollama_url: str = "http://localhost:11434"
    embed_model: str = "bge-m3"
    llm_model: str = "qwen2.5:3b"
    think: bool = False
    num_ctx: int = 4096
    num_gpu: int = 0
    temperature: float = 0.2
    connect_timeout: float = 5
    embed_timeout: float = 180
    llm_timeout: float = 240
    llm_total_timeout: float = 900
    max_upload_mb: int = 50
    max_files: int = 5
    max_chunks: int = 4000
    chunk_size: int = 700
    chunk_overlap: int = 100
    embed_batch_size: int = 4
    top_k: int = 5
    min_similarity: float = 0.40
    history_turns: int = 4
    rewrite_followups: bool = True
    suggestions: bool = True
    duplicate_threshold: float = 0.95
    data_dir: str = "data"
    session_ttl_days: float = 7
    log_level: str = "INFO"
    extra: dict = field(default_factory=dict)

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        return p if p.is_absolute() else ROOT / p

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1024 * 1024)


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _coerce(value, typ):
    if typ is bool or typ == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if typ is int or typ == "int":
        return int(float(value))
    if typ is float or typ == "float":
        return float(value)
    return str(value)


def load_settings(config_path: Path | None = None) -> Settings:
    config_path = config_path or Path(os.environ.get("DOCQA_CONFIG", ROOT / "config.yaml"))
    raw: dict = {}
    if config_path.is_file():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    env = {**_parse_env_file(ROOT / ".env"), **os.environ}
    s = Settings()
    for f in fields(Settings):
        if f.name == "extra":
            continue
        val = raw.get(f.name, getattr(s, f.name))
        env_key = "DOCQA_" + f.name.upper()
        if env_key in env:
            val = env[env_key]
        setattr(s, f.name, _coerce(val, f.type))
    s.chunk_overlap = max(0, min(s.chunk_overlap, s.chunk_size // 2))
    s.top_k = max(1, min(s.top_k, 20))
    return s


settings = load_settings()
