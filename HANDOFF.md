# Ghi chú làm dở — 2026-11-09

## Đã xong
- Build lại image `docqa:latest` (439 MB) và chạy lại stack bằng `docker compose --profile public up -d`.
- Kiểm tra: docqa, ollama đều healthy; caddy chạy. `:8000` trả 200, `:8080` trả 401 (đòi mật khẩu đúng như mong muốn).
- Model trong Ollama: `qwen2.5:7b` (4.7 GB, đặt qua `DOCQA_LLM_MODEL` trong `.env`) và `bge-m3` (1.2 GB).

## Đang làm dở: đẩy image Ollama kèm sẵn model lên Docker Hub
Mục tiêu: `tuanpq-dev/docqa-ollama`, **private**, có sẵn `qwen2.5:7b` + `bge-m3` để máy khác kéo về chạy ngay, khỏi tải model.

Chưa build xong (bị dừng giữa chừng), chưa push gì.

Các bước:

1. Tạo repo **private** `tuanpq-dev/docqa-ollama` trên hub.docker.com trước.
   Nếu để `docker push` tự tạo repo thì repo sẽ thành public.
2. Chép model ra khỏi container (khoảng 5.5 GB):
   ```bash
   mkdir -p ~/ollama-img && docker cp docqa-ollama-1:/root/.ollama/models ~/ollama-img/models
   ```
3. Tạo `~/ollama-img/Dockerfile`:
   ```dockerfile
   FROM ollama/ollama:0.40.9
   COPY models /root/.ollama/models
   ENV OLLAMA_KEEP_ALIVE=24h
   ```
4. Build, kiểm tra rồi push:
   ```bash
   docker build -t tuanpq-dev/docqa-ollama:latest -t tuanpq-dev/docqa-ollama:0.40.9-qwen2.5-7b-bge-m3 ~/ollama-img
   docker run --rm --entrypoint /bin/sh tuanpq-dev/docqa-ollama:latest -c 'ollama serve >/dev/null 2>&1 & sleep 3; ollama list'
   docker login
   docker push --all-tags tuanpq-dev/docqa-ollama
   ```
5. (Tuỳ chọn) Cho `docker-compose.yml` dùng image này thay cho `ollama/ollama:0.40.9`, và bỏ service `ollama-pull`.
   Lưu ý: volume `ollama-models` mount đè lên `/root/.ollama`, nên model chỉ được chép vào volume khi volume còn trống (lần chạy đầu).

## Cần xử lý
- **RAM:** Docker chỉ được cấp khoảng 3.5 GiB, trong khi `qwen2.5:7b` cần khoảng 5 GB khi nạp, nên dễ chạy rất chậm hoặc bị tắt vì hết RAM (OOM).
  Cách xử lý: tăng RAM cho Docker lên khoảng 8 GB, hoặc đổi về `qwen3.5:4b`.
- **Token Cloudflare bị lộ:** `CLOUDFLARE_TUNNEL_TOKEN` trong `.env` đã bị in ra log của phiên làm việc. Nên đổi token trên Cloudflare.
  Nếu không dùng tunnel nữa (stack hiện dùng Tailscale Funnel) thì xoá luôn dòng đó.
