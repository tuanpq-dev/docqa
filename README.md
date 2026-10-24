# docqa – Hỏi đáp tài liệu chạy hoàn toàn trên máy bạn

Kéo thả một file PDF, Word (.docx), .txt hoặc .md vào trang web rồi đặt câu hỏi. docqa đọc file, chia đoạn, tạo vector bằng `bge-m3` và trả lời bằng `qwen3.5:4b` qua **Ollama**, kèm trích dẫn nguồn [1], [2]… Sau khi tải model xong, ứng dụng chạy được **hoàn toàn offline**: không có CDN, không dùng font ngoài, không gửi dữ liệu ra internet.

> Đây là RAG (truy xuất + sinh câu trả lời), **không** phải train model. Tài liệu của bạn không bị đưa vào model.

---

## 1. Cài đặt nhanh trên Windows

Dán **một dòng** vào cửa sổ dòng lệnh, không cần quyền Administrator và không cần cài git:

**PowerShell** (Start → gõ "PowerShell"):

```powershell
irm https://raw.githubusercontent.com/tuanpq-dev/docqa/main/install.ps1 | iex
```

**cmd** (Command Prompt):

```bat
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/tuanpq-dev/docqa/main/install.ps1 | iex"
```

Script kiểm tra trước (Windows, `winget`, mạng, dung lượng ổ đĩa) rồi **chỉ cài những gì còn thiếu**:

1. **Python ≥ 3.10**: tìm qua `py -3` / `python` (bỏ qua bản "giả" của Microsoft Store); chưa có thì `winget install Python.Python.3.12`;
2. **Mã nguồn**: tải file zip từ GitHub vào `%USERPROFILE%\docqa`. Khi cập nhật chỉ tải lại nếu GitHub có bản mới, và **giữ nguyên** `.env`, `data\` (tài liệu đã nạp), `.venv`;
3. **`.venv` + thư viện** trong `requirements.txt` (bằng `.venv\Scripts\python.exe -m pip`), tạo `.env` từ `.env.example` và thư mục `data\`;
4. **Ollama**: tìm trong PATH và `%LOCALAPPDATA%\Programs\Ollama`; chưa có thì `winget install Ollama.Ollama`; chưa chạy thì khởi động ẩn và đợi tối đa 30 giây;
5. **Model** trong `config.yaml`: `bge-m3` (~1,2 GB) và `qwen3.5:4b` (~3,3 GB). Model nào đã có trong `ollama list` thì bỏ qua.

Chạy lại bao nhiêu lần cũng được: lần sau chỉ mất vài giây. Cài xong, mỗi lần muốn dùng thì **nhấp đúp `run.bat`** trong `%USERPROFILE%\docqa`, hoặc:

- PowerShell: `& "$HOME\docqa\run.bat"`
- cmd: `"%USERPROFILE%\docqa\run.bat"`

`run.bat` chạy được từ bất kỳ thư mục nào, dùng Python trong `.venv`, tự bật Ollama nếu chưa chạy, rồi mở trình duyệt tại http://localhost:8000.

> Thông báo của trình cài là tiếng Việt **không dấu**. Lý do kỹ thuật: file phải lưu UTF-8 không BOM để `irm | iex` chạy được, mà Windows PowerShell 5.1 đọc file không BOM theo bảng mã ANSI khi chạy bằng `-File`.

### Xem script trước khi chạy

`irm … | iex` nghĩa là chạy ngay script tải về. Muốn đọc trước:

```powershell
irm https://raw.githubusercontent.com/tuanpq-dev/docqa/main/install.ps1 -OutFile install.ps1
notepad install.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -DryRun   # chỉ in ra những việc sẽ làm
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1           # cài thật
```

Với dòng lệnh một dòng, bật chế độ thử bằng biến môi trường: `$env:DOCQA_DRYRUN=1; irm … | iex` (PowerShell) hoặc `set DOCQA_DRYRUN=1` trước lệnh `powershell …` (cmd). Dry-run vẫn kiểm tra thật cái gì đã có / chưa có, nhưng không cài, không tải, không ghi file nào.

### Cấu hình tối thiểu

| | Tối thiểu | Khuyên dùng |
|---|---|---|
| Hệ điều hành | Windows 10 **1809** (build 17763) trở lên hoặc Windows 11, có `winget` (App Installer) | Windows 11 |
| RAM | 8 GB | 16 GB (khi trả lời, `qwen3.5:4b` + `bge-m3` chiếm khoảng 5–6 GB) |
| Ổ đĩa trống | ~8 GB, chủ yếu trên ổ `C:` (model trong `%USERPROFILE%\.ollama`, Ollama trong `%LOCALAPPDATA%`, `.venv` ~0,6 GB) | 15 GB nếu muốn thử model 7B |
| GPU | **Không bắt buộc** – mặc định chạy CPU (`num_gpu: 0`) | GPU NVIDIA ≥ 6 GB thì đặt `num_gpu: -1` trong `config.yaml` |
| Mạng | Cần khi cài và tải model; dùng xong thì chạy offline | |

**Máy RAM dưới 12 GB** (ví dụ laptop 8 GB, i5 đời 10): trình cài tự dùng `qwen2.5:3b` (~1,9 GB, nhẹ hơn khoảng 1,4 GB RAM khi chạy) thay cho `qwen3.5:4b`, và ghi `DOCQA_LLM_MODEL=qwen2.5:3b` vào `.env` để lần sau giữ nguyên. Chỉ áp dụng khi bạn chưa tự chọn model (`DOCQA_MODEL`, `.env` hoặc `config.yaml`). Tắt bằng `DOCQA_AUTO_MODEL=0`; muốn quay lại `qwen3.5:4b` thì xóa dòng đó trong `.env`. Với máy 8 GB, chỉ RAM là giới hạn thật: CPU i5 đời 10 chạy được cả hai model, `qwen2.5:3b` chỉ nhanh hơn chút.

### Tùy chọn khi cài (biến môi trường)

| Biến | Ý nghĩa |
|---|---|
| `DOCQA_DIR` | Thư mục cài (mặc định `%USERPROFILE%\docqa`) |
| `DOCQA_MODEL` | Model trả lời, ví dụ `qwen2.5:7b` (ghi vào `.env` thành `DOCQA_LLM_MODEL`, tên khóa app đọc) |
| `DOCQA_EMBED_MODEL` | Model tạo vector (đổi thì phải nạp lại tài liệu) |
| `DOCQA_AUTO_MODEL=0` | Không tự chuyển sang `qwen2.5:3b` khi máy ít RAM |
| `DOCQA_RAM_GB` | Giả lập dung lượng RAM (GB) để thử logic chọn model, ví dụ `8` |
| `DOCQA_DRYRUN=1` | Chỉ in ra những việc sẽ làm (giống `-DryRun`) |
| `DOCQA_NO_UPDATE=1` | Không tải lại mã nguồn khi đã cài |
| `DOCQA_OLLAMA_URL` | Dùng Ollama ở máy khác (khi đó không cài Ollama trên máy này) |
| `DOCQA_REPO`, `DOCQA_BRANCH` | Cài từ fork / nhánh khác (`chu/repo` hoặc URL GitHub) |

### Đổi model

```powershell
$env:DOCQA_MODEL='qwen2.5:7b'; irm https://raw.githubusercontent.com/tuanpq-dev/docqa/main/install.ps1 | iex
```

Script ghi `DOCQA_LLM_MODEL=qwen2.5:7b` vào `.env` và tải model mới (model cũ vẫn giữ). Hoặc sửa `llm_model` trong `%USERPROFILE%\docqa\config.yaml` rồi chạy `run.bat` – nó cũng tự tải model còn thiếu. Lưu ý `.env` không hỗ trợ chú thích `#` ở cuối dòng. Xem thêm mục 4.

### Gỡ cài đặt

1. Tắt docqa (Ctrl+C trong cửa sổ `run.bat`).
2. Xóa thư mục cài – gồm cả `.venv` và `data\` (tài liệu đã nạp): `Remove-Item -Recurse -Force "$HOME\docqa"`.
3. Xóa model: `ollama rm bge-m3 qwen3.5:4b`.
4. Gỡ Ollama nếu không dùng nữa: `winget uninstall Ollama.Ollama` (hoặc Settings → Apps), rồi xóa thư mục `%USERPROFILE%\.ollama` nếu còn.
5. Python do script cài (`winget uninstall Python.Python.3.12`) có thể giữ lại nếu chương trình khác cần.

### Xử lý sự cố khi cài

| Hiện tượng | Cách xử lý |
|---|---|
| `khong co winget` / `'winget' is not recognized` | Thường gặp trên Windows 10 cũ, bản LTSC hoặc máy công ty. Cài **App Installer** từ Microsoft Store (https://aka.ms/getwinget) rồi chạy lại. Hoặc tự cài [Python 3.12](https://www.python.org/downloads/) (tick **Add python.exe to PATH**) và [Ollama](https://ollama.com/download/windows), mở cửa sổ mới rồi chạy lại lệnh cài – script sẽ không cần winget nữa |
| Hộp thoại **UAC** ("Do you want to allow this app to make changes…") | Trình cài Python/Ollama có thể hỏi quyền: chọn **Yes**. Nếu là máy công ty không có quyền quản trị, nhờ IT cài Python và Ollama, hoặc tự cài bản cho riêng người dùng (`winget install Python.Python.3.12 --scope user`), rồi chạy lại |
| Antivirus / Windows Defender cảnh báo, chặn lệnh `irm … \| iex`, hoặc báo `Access is denied` | Một số phần mềm diệt virus chặn kiểu "tải rồi chạy ngay" dù script không có gì độc hại. Tải file về, đọc, rồi chạy bằng `-File` như mục **Xem script trước khi chạy**. SmartScreen hỏi khi chạy trình cài Ollama thì chọn **More info → Run anyway** (trình cài lấy từ winget/ollama.com) |
| `running scripts is disabled on this system` | Chính sách thực thi chặn file `.ps1`. Chạy kèm `-ExecutionPolicy Bypass` như các lệnh ở trên (chỉ áp dụng cho lần chạy đó, không đổi cài đặt máy) |
| Vừa cài xong nhưng báo không tìm thấy Python / Ollama | PATH của cửa sổ cũ chưa cập nhật. Đóng cửa sổ, mở cửa sổ mới rồi chạy lại lệnh cài |
| `Khong du dung luong` | Dọn ổ `C:`, hoặc cài vào ổ khác: `$env:DOCQA_DIR='D:\docqa'` và đặt biến môi trường người dùng `OLLAMA_MODELS=D:\ollama-models` (Settings → "Edit environment variables for your account"), khởi động lại Ollama rồi chạy lại |
| `Khong co ket noi mang` / tải model dừng giữa chừng | Kiểm tra mạng, proxy, tường lửa (cần truy cập `github.com`, `raw.githubusercontent.com`, `ollama.com`). Chạy lại – model đã tải dở sẽ được tải tiếp |
| `Ollama khong khoi dong duoc sau 30 giay` | Mở **Ollama** từ Start menu, đợi biểu tượng hiện ở khay hệ thống rồi chạy lại. Log ở `data\ollama.log` |
| `Khong doc duoc cau hinh` | Có lỗi trong `config.yaml` hoặc `.env` (dòng "Chi tiet" cho biết khóa nào). `.env` không hỗ trợ chú thích `#` cuối dòng |

## 2. Cài đặt nhanh trên macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/tuanpq-dev/docqa/main/install.sh | bash
```

Cùng logic với bản Windows: kiểm tra và chỉ cài phần còn thiếu – `git`, `curl`, Python ≥ 3.10 (qua `apt` / `dnf` / `pacman` / Homebrew; Debian/Ubuntu cài thêm `python3-venv`), mã nguồn vào `~/docqa` (`git clone`, lần sau `git pull --ff-only`), `.venv` + thư viện, `.env`, `data/`, Ollama (Linux: script chính thức `ollama.com/install.sh`, kèm `zstd`; macOS: Homebrew hoặc app chính thức) và các model. `sudo` chỉ dùng khi cần cài gói hệ thống. Cài xong: `cd ~/docqa && ./run.sh`.

Xem trước / chạy thử:

```bash
curl -fsSL https://raw.githubusercontent.com/tuanpq-dev/docqa/main/install.sh -o install.sh
less install.sh
bash install.sh --dry-run
bash install.sh
```

Các biến môi trường giống bản Windows (`DOCQA_DIR`, `DOCQA_MODEL`, `DOCQA_EMBED_MODEL`, `DOCQA_AUTO_MODEL`, `DOCQA_DRYRUN`, `DOCQA_NO_UPDATE`, `DOCQA_OLLAMA_URL`, `DOCQA_REPO`, `DOCQA_BRANCH`), ví dụ `curl -fsSL …/install.sh | DOCQA_MODEL=qwen2.5:7b bash`. Cấu hình máy như bảng ở mục 1; Linux cần Python ≥ 3.10 (Ubuntu 22.04+, Debian 12+, Fedora, Arch – Ubuntu 20.04 / Debian 11 phải tự cài Python mới hơn trước).

Gỡ cài đặt: `rm -rf ~/docqa`, `ollama rm bge-m3 qwen3.5:4b`, rồi gỡ Ollama nếu không dùng nữa (macOS: `brew uninstall ollama` hoặc xóa `Ollama.app` và `~/.ollama`; Linux: theo [hướng dẫn chính thức](https://github.com/ollama/ollama/blob/main/docs/linux.md#uninstall)).

## 3. Chạy nhanh (khi đã có Python và Ollama)

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
4. `ollama pull` các model còn thiếu (`bge-m3` ~1,2 GB, `qwen3.5:4b` ~3,3 GB – chỉ lần đầu),
5. khởi động server và mở trình duyệt tại **http://localhost:8000**.

Từ lần chạy thứ hai, server khởi động gần như ngay lập tức.

Tùy chọn: `./run.sh --no-browser` (không mở trình duyệt), `./run.sh --no-pull` (không tự tải model).

Chạy thủ công (không dùng script):

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
ollama pull bge-m3 && ollama pull qwen3.5:4b
python -m app.main
```

## 4. Đổi model và cấu hình

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
| `llm_model` | `qwen3.5:4b` | Model sinh câu trả lời (nhẹ hơn: `qwen2.5:3b`) |
| `think` | `false` | Bật chế độ "suy nghĩ" của model Qwen3.x (chậm hơn nhiều trên CPU) |
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

## 5. Cách hoạt động

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

## 6. Xử lý lỗi thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| Banner "Ollama chưa chạy" | Mở app Ollama, hoặc chạy `ollama serve`, rồi bấm **Kiểm tra lại** |
| Banner "Chưa tải đủ model" | Chạy lệnh hiện trên banner, ví dụ `ollama pull bge-m3` |
| "PDF này có vẻ là bản scan" | docqa không có OCR. Chạy OCR trước (`ocrmypdf in.pdf out.pdf`, Adobe Acrobat, hoặc mở bằng Google Docs) rồi tải lại |
| "File quá lớn" | Giới hạn 50 MB (`max_upload_mb`). Tách file thành phần nhỏ hơn |
| "File bị hỏng" / "được bảo vệ bằng mật khẩu" | Mở file rồi lưu/xuất lại (In ra PDF mới) |
| File `.doc` (Word cũ) | Lưu lại thành `.docx` hoặc `.pdf` |
| Tạo vector/trả lời cực chậm, câu trả lời rỗng, log Ollama có `ErrorOutOfDeviceMemory` | Ollama đang đẩy model lên GPU yếu (card rời 2 GB, driver NVK/nouveau, iGPU qua Vulkan). docqa mặc định `num_gpu: 0` để chạy CPU – kiểm tra bạn không đổi giá trị này. Nếu có GPU mạnh (NVIDIA driver chính hãng, ≥ 6 GB) thì đặt `num_gpu: -1` |
| Trả lời rất chậm | Bình thường trên CPU: lần đầu Ollama phải nạp model vào RAM (10–30 giây). Dùng model nhẹ hơn (`qwen2.5:3b`), giảm `top_k` xuống 3–4, đóng bớt ứng dụng nặng |
| "Tài liệu có vẻ không liên quan" khi câu hỏi đúng chủ đề | Hạ `min_similarity` (ví dụ 0.3) hoặc hỏi bằng từ ngữ có trong tài liệu |
| Cổng 8000 đang bị dùng | Đặt `DOCQA_PORT=8080` hoặc sửa `port` trong `config.yaml` |
| Debian/Ubuntu: lỗi tạo venv | `sudo apt install python3-venv` |
| Muốn xóa toàn bộ dữ liệu | Bấm **Xóa tài liệu & lịch sử**, hoặc xóa thư mục `data/` |

## 7. Quyết định thiết kế (chỗ đề bài để ngỏ)

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

## 8. Phát triển & kiểm thử

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
scripts/   ensure_deps.py (cài thư viện khi requirements.txt đổi) · install_helper.py (dùng chung cho install.sh / install.ps1)
data/      dữ liệu phiên (không commit)
```
