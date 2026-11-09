# syntax=docker/dockerfile:1
# docqa – image chỉ chứa web server. Ollama chạy riêng (trên máy host hoặc container khác).
#
#   docker build -t docqa .
#   docker run -d --name docqa -p 8000:8000 \
#     --add-host=host.docker.internal:host-gateway \
#     -v docqa-data:/data docqa
#
# Đổi model / Ollama URL bằng biến môi trường DOCQA_*, ví dụ:
#   -e DOCQA_OLLAMA_URL=http://ollama:11434 -e DOCQA_LLM_MODEL=qwen2.5:7b

# ---------- build: cài thư viện vào venv ----------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install -r requirements.txt

# ---------- runtime ----------
FROM python:3.12-slim

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOCQA_HOST=0.0.0.0 \
    DOCQA_PORT=8000 \
    DOCQA_DATA_DIR=/data \
    DOCQA_OLLAMA_URL=http://host.docker.internal:11434

RUN useradd --create-home --uid 1000 docqa \
    && mkdir -p /data && chown docqa:docqa /data

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=docqa:docqa config.yaml ./
COPY --chown=docqa:docqa app ./app
COPY --chown=docqa:docqa static ./static

USER docqa
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"DOCQA_PORT\",\"8000\")}/api/health', timeout=4)" || exit 1

CMD ["python", "-m", "app.main"]
