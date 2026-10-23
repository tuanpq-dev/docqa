# docqa – Hỏi đáp tài liệu chạy hoàn toàn trên máy bạn

Kéo thả một file PDF, Word (.docx), .txt hoặc .md vào trang web rồi đặt câu hỏi. docqa đọc file, chia đoạn, tạo vector bằng `bge-m3` và trả lời bằng `qwen2.5` qua **Ollama**, kèm trích dẫn nguồn [1], [2]… Sau khi tải model xong, ứng dụng chạy được **hoàn toàn offline**: không có CDN, không dùng font ngoài, không gửi dữ liệu ra internet.

> Đây là RAG (truy xuất + sinh câu trả lời), **không** phải train model. Tài liệu của bạn không bị đưa vào model.

---

## 1. Chạy nhanh

Cần cài trước:
- **Python 3.10+** – https://www.python.org/downloads/
- **Ollama** – https://ollama.com/download

Sau đó:

```bash
# macOS / Linux
./run.sh

# Windows: nhấp đúp run.bat, hoặc trong cmd:
run.bat
```

Script sẽ tự động:
1. kiểm tra Python và Ollama (nếu thiếu thì in hướng dẫn cài),
2. tạo `.venv` và cài thư viện (chỉ lần đầu, hoặc khi `requirements.txt` thay đổi),
3. bật `ollama serve` nếu Ollama chưa chạy,
4. `ollama pull` các model còn thiếu (`bge-m3` ~1,2 GB, `qwen2.5:3b` ~1,9 GB – chỉ lần đầu),
5. khởi động server và mở trình duyệt tại **http://localhost:8000**.

Từ lần chạy thứ hai, server khởi động gần như ngay lập tức.

Tùy chọn: `./run.sh --no-browser` (không mở trình duyệt), `./run.sh --no-pull` (không tự tải model).

Chạy thủ công (không dùng script):

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
ollama pull bge-m3 && ollama pull qwen2.5:3b
python -m app.main
```

## 2. Đổi model và cấu hình

Mọi cấu hình nằm trong `config.yaml`. Mỗi khóa đều ghi đè được bằng biến môi trường `DOCQA_<TÊN_KHÓA_IN_HOA>` hoặc file `.env` (xem `.env.example`). Thứ tự ưu tiên: biến môi trường > `.env` > `config.yaml`.

Dùng model 7B (trả lời tốt hơn, chậm hơn khoảng 2–3 lần trên CPU):

```bash
DOCQA_LLM_MODEL=qwen2.5:7b ./run.sh                # macOS / Linux
set DOCQA_LLM_MODEL=qwen2.5:7b && run.bat          # Windows (cmd)
```

hoặc sửa `llm_model: qwen2.5:7b` trong `config.yaml`. Script sẽ tự `ollama pull` model mới.

Các khóa hay dùng:

| Khóa | Mặc định | Ý nghĩa |
|---|---|---|
| `llm_model` | `qwen2.5:3b` | Model sinh câu trả lời |
| `embed_model` | `bge-m3` | Model tạo vector (đổi model này thì phải nạp lại tài liệu) |
| `port` / `host` | `8000` / `127.0.0.1` | Server chỉ nghe trên máy này |
| `top_k` | `5` | Số đoạn được lấy làm ngữ cảnh |
| `min_similarity` | `0.40` | Dưới ngưỡng này thì báo "tài liệu có vẻ không liên quan" và không gọi LLM |
| `chunk_size` / `chunk_overlap` | `700` / `100` | Độ dài đoạn và phần gối nhau (ký tự) |
| `history_turns` | `4` | Số lượt hỏi–đáp gần nhất được nhớ |
| `rewrite_followups` | `true` | Viết lại câu hỏi nối tiếp thành câu độc lập trước khi tìm |
| `suggestions` | `true` | Sinh 3 câu hỏi gợi ý sau khi nạp |
| `max_upload_mb` | `50` | Giới hạn kích thước file |
| `max_files` | `5` | Số file tối đa mỗi phiên |
| `session_ttl_days` | `7` | Tự xóa phiên không dùng quá N ngày |
| `llm_timeout` / `llm_total_timeout` | `240` / `900` | Giây chờ tối đa giữa hai token / cho cả câu trả lời |

## 3. Cách hoạt động

```
File ──► đọc (PyMuPDF / python-docx) ──► chuẩn hóa Unicode NFC ──► chia đoạn ~700 ký tự, gối 100
     ──► bge-m3 tạo vector theo batch ──► lưu numpy (data/<session>/vectors.npy)

Câu hỏi ──► (nếu là câu nối tiếp) LLM viết lại thành câu độc lập
        ──► bge-m3 tạo vector ──► trộn cosine + từ khóa (BM25) ──► điểm cao nhất < ngưỡng? → báo không liên quan
        ──► prompt "chỉ dùng các đoạn [1]..[k], trích dẫn, không bịa" ──► qwen2.5 stream từng token (SSE)
```

- **Chia đoạn** ưu tiên cắt theo đoạn văn → câu → từ; không bao giờ cắt rời dấu tiếng Việt khỏi chữ. Mỗi đoạn giữ tên file và trang bắt đầu/kết thúc (với PDF).
- **Bản đồ vector**: chiếu các vector 1024 chiều xuống 2D bằng PCA (numpy SVD). Màu chấm chuyển dần theo vị trí đoạn trong tài liệu; khi hỏi, câu hỏi hiện là ngôi sao, các đoạn được truy xuất sáng lên và nối bằng đường mảnh (càng đậm càng giống). Đoạn "gần trùng lặp" là cặp có cosine ≥ 0,95.
- **Ngôn ngữ trả lời** theo ngôn ngữ câu hỏi (nhận diện dấu tiếng Việt).
- **Dữ liệu**: mỗi trình duyệt có một session id (localStorage); vector và đoạn văn nằm trong `data/<session_id>/`; lịch sử chat nằm trong localStorage của trình duyệt. File gốc không được lưu lại – chỉ lưu văn bản đã trích.

### API (cho ai muốn tích hợp)

Mọi request cần header `X-Session-Id` (8–64 ký tự `A-Z a-z 0-9 _ -`).

| Method | Đường dẫn | Mô tả |
|---|---|---|
| GET | `/api/health` | Trạng thái Ollama và model |
| POST | `/api/upload?filename=...&replace=<doc_id>` | Body là nội dung file thô; trả về SSE `progress` / `done` / `error` |
| GET | `/api/doc` | Tài liệu đã nạp, thống kê, câu hỏi gợi ý |
| DELETE | `/api/doc/{doc_id}` | Gỡ một tài liệu |
| GET | `/api/chunk/{chunk_id}` | Nội dung đầy đủ một đoạn |
| GET | `/api/map` | Tọa độ PCA của mọi đoạn và các cặp gần trùng lặp |
| POST | `/api/chat` | `{question, history}` → SSE `status` / `rewritten` / `sources` / `token` / `done` / `error` |
| DELETE | `/api/session` | Xóa sạch dữ liệu của phiên trên đĩa |

## 4. Xử lý lỗi thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| Banner "Ollama chưa chạy" | Mở app Ollama, hoặc chạy `ollama serve`, rồi bấm **Kiểm tra lại** |
| Banner "Chưa tải đủ model" | Chạy lệnh hiện trên banner, ví dụ `ollama pull bge-m3` |
| "PDF này có vẻ là bản scan" | docqa không có OCR. Chạy OCR trước (`ocrmypdf in.pdf out.pdf`, Adobe Acrobat, hoặc mở bằng Google Docs) rồi tải lại |
| "File quá lớn" | Giới hạn 50 MB (`max_upload_mb`). Tách file thành phần nhỏ hơn |
| "File bị hỏng" / "được bảo vệ bằng mật khẩu" | Mở file rồi lưu/xuất lại (In ra PDF mới) |
| File `.doc` (Word cũ) | Lưu lại thành `.docx` hoặc `.pdf` |
| Tạo vector/trả lời cực chậm, câu trả lời rỗng, log Ollama có `ErrorOutOfDeviceMemory` | Ollama đang đẩy model lên GPU yếu (card rời 2 GB, driver NVK/nouveau, iGPU qua Vulkan). docqa mặc định `num_gpu: 0` để chạy CPU – kiểm tra bạn không đổi giá trị này. Nếu có GPU mạnh (NVIDIA driver chính hãng, ≥ 6 GB) thì đặt `num_gpu: -1` |
| Trả lời rất chậm | Bình thường trên CPU: lần đầu Ollama phải nạp model vào RAM (10–30 giây). Dùng `qwen2.5:3b`, giảm `top_k` xuống 3–4, đóng bớt ứng dụng nặng |
| "Tài liệu có vẻ không liên quan" khi câu hỏi đúng chủ đề | Hạ `min_similarity` (ví dụ 0.3) hoặc hỏi bằng từ ngữ có trong tài liệu |
| Cổng 8000 đang bị dùng | Đặt `DOCQA_PORT=8080` hoặc sửa `port` trong `config.yaml` |
| Debian/Ubuntu: lỗi tạo venv | `sudo apt install python3-venv` |
| Muốn xóa toàn bộ dữ liệu | Bấm **Xóa tài liệu & lịch sử**, hoặc xóa thư mục `data/` |

## 5. Quyết định thiết kế (chỗ đề bài để ngỏ)

- **Upload gửi file thô** (không dùng multipart) và trả tiến độ bằng SSE ngay trong cùng request. Server đếm byte khi nhận và cắt ngay khi vượt 50 MB.
- **Nhiều file**: tối đa 5 file một phiên; mỗi thẻ tài liệu có nút **Thay file** và **Gỡ**. Gỡ tài liệu cuối cùng thì lịch sử chat cũng bị xóa (vì chat gắn với tài liệu).
- **Word (.docx) không có số trang** (Word chỉ tính trang lúc hiển thị), nên nguồn từ .docx ghi số thứ tự đoạn thay cho số trang. Bảng trong Word được giữ dưới dạng `ô | ô`.
- **Đoạn văn trong PDF** được đoán từ độ dài dòng (PDF thường không có dòng trống giữa các đoạn).
- **Câu "không tìm thấy"**: câu hỏi tiếng Việt → "Không tìm thấy trong tài liệu.", câu hỏi tiếng Anh → "Not found in the document."
- **Câu hỏi nối tiếp**: khi đã có lịch sử, LLM viết lại câu hỏi trước khi tìm (thêm khoảng 2–5 giây trên CPU). Câu đã viết lại hiện mờ phía trên câu trả lời. Tắt bằng `rewrite_followups: false`.
- **Câu hỏi gợi ý** chạy nền sau khi nạp; nếu người dùng hỏi trước khi gợi ý xong thì việc sinh gợi ý bị hủy để nhường CPU cho câu hỏi.
- **Chạy CPU mặc định (`num_gpu: 0`)**: đo thực tế trên laptop i7-1165G7 + MX330 (driver NVK), Ollama tự đẩy model lên GPU làm embedding chậm hơn ~10–60 lần và đôi khi trả lời rỗng do hết bộ nhớ GPU. Ép CPU: ~0,5 giây/đoạn khi tạo vector, ~20–30 giây/câu trả lời với qwen2.5:3b.
- **Nạp file dài chịu được máy chậm**: mỗi batch (4 đoạn) quá giờ thì tự chia nhỏ và thử lại; lỗi tạm thời được thử lại. Vector đã tạo được lưu ngay vào `data/<session>/embcache/`, nên nếu nạp thất bại giữa chừng, nạp lại **cùng file** sẽ chạy tiếp từ chỗ dừng. Thanh tiến độ hiện thời gian còn lại ước tính.
- **Đọc PDF bằng PyMuPDF** thay vì pypdf: với giáo trình tiếng Việt thực tế, pypdf chèn dấu cách giữa âm tiết ("tri ết h ọc") ở 99% số đoạn khiến tìm kiếm gần như hỏng; PyMuPDF đọc đúng và nhanh hơn ~20 lần. pypdf chỉ còn là dự phòng. Lưu ý giấy phép PyMuPDF là AGPL – thoải mái cho dùng cá nhân. Số trang in ở đầu/cuối trang được lọc bỏ.
- **Tìm kiếm kết hợp vector + từ khóa (BM25)** bằng Reciprocal Rank Fusion: thuật ngữ chính xác ("phép biện chứng duy vật") được bắt tốt hơn. Đoạn **liền sau** đoạn khớp nhất được gửi kèm (lời giải trong giáo trình thường nằm ngay sau tiêu đề).
- **Copy câu hỏi từ giáo trình**: mục "Câu hỏi ôn tập/Bài tập" chứa đúng câu hỏi nên trước đây luôn được tìm thấy đầu tiên thay cho đoạn có lời giải. Giờ các đoạn toàn câu hỏi bị hạ hạng; tiền tố kiểu "Câu 3 (2 điểm):" được bỏ khi tìm.
- **"Đoạn này ở trang nào?"**: trả lời trực tiếp từ dữ liệu, không qua LLM (vài giây, không bịa số trang). Dán đoạn văn trong ngoặc kép hoặc sau dấu ":" để khớp nguyên văn; nếu không khớp, liệt kê 3 vị trí liên quan nhất. Số trang tính theo file PDF (có thể lệch với số in trong sách).
- **Câu hỏi độc lập không dùng lịch sử**: chỉ khi câu hỏi có dấu hiệu nối tiếp ("còn…thì sao", "cái thứ hai", "nó", câu rất ngắn…) mới viết lại câu hỏi và kèm lượt trước (dạng ghi chú, không phải lượt chat). Các lượt "Không tìm thấy trong tài liệu" không được gửi làm lịch sử – trước đây chúng khiến model trả lời lại câu hỏi cũ.
- **Câu hỏi nối tiếp**: nếu model không viết lại được (model 3B đôi khi bỏ qua "cái thứ hai…"), docqa tìm bằng câu hỏi trước + câu hiện tại, và luôn gửi kèm lượt hỏi–đáp gần nhất cho model.
- **Ước lượng token** là xấp xỉ (≈1,35 token/từ), chỉ để tham khảo.
- **Ngưỡng `min_similarity` = 0.40** chọn theo đo thực tế với bge-m3: câu không liên quan cho cosine ~0,30–0,35, câu liên quan ~0,5–0,75. Nên chỉnh theo tài liệu của bạn.
- **Phiên hết hạn**: phiên không dùng quá `session_ttl_days` ngày bị xóa trên đĩa (kiểm tra lúc khởi động và mỗi 6 giờ). Khi mở lại trình duyệt, lịch sử chat cũ của phiên đó cũng được xóa cho khớp.
- **Bảo mật**: chỉ nghe `127.0.0.1`; kiểm tra header Host (chống DNS rebinding); Content-Security-Policy chặn mọi script/kết nối ra ngoài; session id và tên file được kiểm tra/làm sạch để chống path traversal; Markdown được escape HTML trước khi render (chống XSS). Log không chứa nội dung tài liệu hay câu hỏi.

## 6. Phát triển & kiểm thử

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Test không cần Ollama: `tests/fake_ollama.py` là một Ollama giả lập (embedding "túi từ" + câu trả lời mẫu). Có thể dùng nó để thử giao diện mà không cần model thật:

```bash
.venv/bin/python -m tests.fake_ollama --port 11500 &
DOCQA_OLLAMA_URL=http://127.0.0.1:11500 DOCQA_MIN_SIMILARITY=0.1 .venv/bin/python -m app.main
```

Tài liệu mẫu nằm trong `tests/fixtures/` (tạo lại bằng `tests/make_fixtures.py`, cần `reportlab`).

Cấu trúc:

```
app/       main.py (API) · ingest.py (đọc file) · chunking.py · embeddings.py (gọi Ollama)
           store.py (vector numpy) · rag.py (prompt, viết lại câu hỏi, gợi ý) · analysis.py (PCA, trùng lặp)
           sessions.py (tách/xóa/dọn phiên) · config.py · launcher.py (dùng cho run.sh/run.bat)
static/    index.html · style.css · app.js (JS thuần, không build)
tests/     pytest + Ollama giả lập + tài liệu mẫu
data/      dữ liệu phiên (không commit)
```
