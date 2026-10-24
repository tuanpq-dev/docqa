#!/usr/bin/env bash
# docqa – cài đặt bằng một lệnh trên Linux / macOS:
#
#   curl -fsSL https://raw.githubusercontent.com/tuanpq-dev/docqa/main/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --dry-run      # chỉ in ra những việc sẽ làm
#
# Tự kiểm tra và cài những gì còn thiếu: git, curl, Python >= 3.10 (+ venv), thư viện Python,
# Ollama, và các model trong config.yaml. Chạy lại bao nhiêu lần cũng được: thứ đã có sẽ được bỏ qua.
#
# Biến môi trường:
#   DOCQA_DIR          thư mục cài (mặc định ~/docqa; nếu chạy ./install.sh trong repo thì dùng chính repo đó)
#   DOCQA_REPO         địa chỉ git (mặc định https://github.com/tuanpq-dev/docqa.git)
#   DOCQA_BRANCH       nhánh (mặc định main)
#   DOCQA_NO_UPDATE=1  không git pull khi đã có sẵn
#   DOCQA_MODEL (hoặc DOCQA_LLM_MODEL), DOCQA_EMBED_MODEL   đổi model (được ghi vào .env để app dùng đúng model này)
#   DOCQA_OLLAMA_URL   dùng Ollama ở địa chỉ khác (khi đó không cài Ollama trên máy này)
#   DOCQA_DRYRUN=1     giống --dry-run
#
# Toàn bộ script nằm trong hàm main, gọi ở dòng cuối: bash phải đọc hết file trước khi chạy,
# nên "curl | bash" an toàn kể cả khi một lệnh con đọc stdin.
set -euo pipefail

main() {
  local DOCQA_REPO="${DOCQA_REPO:-https://github.com/tuanpq-dev/docqa.git}"
  local DOCQA_BRANCH="${DOCQA_BRANCH:-main}"
  local DRY_RUN="${DOCQA_DRYRUN:-${DOCQA_DRY_RUN:-0}}"
  # DOCQA_MODEL là tên ngắn (giống install.ps1); app đọc DOCQA_LLM_MODEL
  if [ -n "${DOCQA_MODEL:-}" ]; then export DOCQA_LLM_MODEL="$DOCQA_MODEL"; fi
  local PY_MIN="3.10"
  local DISK_APP_MB=1500      # .venv + thư viện (~600 MB) có dư
  local DISK_OLLAMA_MB=4000   # Ollama cho Linux kèm thư viện GPU khá nặng
  local DISK_MODELS_MB=6000   # bge-m3 (~1,2 GB) + LLM mặc định (~3,3 GB) có dư

  while [ $# -gt 0 ]; do
    case "$1" in
      --dry-run|-n) DRY_RUN=1 ;;
      -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]:-/dev/null}" 2>/dev/null | sed 's/^# \{0,1\}//' || true
                 echo "Tùy chọn: --dry-run  chỉ in ra những việc sẽ làm"; return 0 ;;
      *) die "Tùy chọn không hợp lệ: $1 (dùng --dry-run hoặc --help)" ;;
    esac
    shift
  done
  [ "$DRY_RUN" = "1" ] && say "CHẾ ĐỘ DRY-RUN: chỉ kiểm tra và in ra các việc sẽ làm, không thay đổi gì."

  # ---------------------------------------------------------------- hệ điều hành / trình quản lý gói
  local OS PM=""
  case "$(uname -s)" in
    Linux) OS=linux ;;
    Darwin) OS=mac ;;
    MINGW*|MSYS*|CYGWIN*) die "Đây là Windows. Hãy chạy trong PowerShell: irm https://raw.githubusercontent.com/tuanpq-dev/docqa/main/install.ps1 | iex" ;;
    *) die "Hệ điều hành chưa được hỗ trợ: $(uname -s)" ;;
  esac
  if [ "$OS" = mac ]; then
    if command -v brew >/dev/null 2>&1; then PM=brew; fi
  else
    for p in apt-get dnf pacman; do
      if command -v "$p" >/dev/null 2>&1; then PM="$p"; break; fi
    done
  fi
  say "Hệ điều hành: $OS, trình quản lý gói: ${PM:-không có}"

  # ---------------------------------------------------------------- 1. git, curl
  step "1/6 Công cụ cơ bản (git, curl)"
  local need=()
  command -v git >/dev/null 2>&1 || need+=(git)
  command -v curl >/dev/null 2>&1 || need+=(curl)
  if [ ${#need[@]} -eq 0 ]; then
    ok "đã có git và curl"
  else
    if [ "$OS" = mac ] && [ -z "$PM" ]; then
      die "Thiếu ${need[*]}. Chạy 'xcode-select --install' (hoặc cài Homebrew: https://brew.sh) rồi chạy lại."
    fi
    [ "$PM" = apt-get ] && need+=(ca-certificates)
    pkg_install "${need[@]}"
  fi
  check_net

  # ---------------------------------------------------------------- 2. Python
  step "2/6 Python >= $PY_MIN (kèm venv)"
  local PY
  PY="$(find_python)"
  if [ -n "$PY" ]; then
    ok "dùng $PY ($("$PY" -c 'import sys; print(sys.version.split()[0])'))"
  else
    case "$PM" in
      apt-get) pkg_install python3 python3-venv ;;
      dnf) pkg_install python3 ;;
      pacman) pkg_install python ;;
      brew) pkg_install python@3.12 ;;
      *) if [ "$OS" = mac ]; then
           die "Không có Python $PY_MIN+. Tải tại https://www.python.org/downloads/ hoặc cài Homebrew (https://brew.sh) rồi chạy lại."
         fi
         die "Không có Python $PY_MIN+ và không nhận ra trình quản lý gói. Hãy tự cài Python $PY_MIN+ (kèm venv) rồi chạy lại." ;;
    esac
    hash -r
    PY="$(find_python)"
    if [ -z "$PY" ]; then
      if [ "$DRY_RUN" = 1 ]; then
        PY=python3
      else
        die "Đã cài nhưng vẫn không có Python $PY_MIN+ dùng được venv (bản phân phối có thể quá cũ: $(python3 --version 2>&1 || echo 'không có python3')).
         Hãy cài Python $PY_MIN+ thủ công (Ubuntu cũ: dùng PPA deadsnakes) rồi chạy lại."
      fi
    else
      ok "dùng $PY"
    fi
  fi

  # ---------------------------------------------------------------- 3. mã nguồn
  step "3/6 Mã nguồn docqa"
  local DIR script_dir=""
  if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  fi
  if [ -z "${DOCQA_DIR:-}" ] && [ -n "$script_dir" ] && [ -f "$script_dir/app/launcher.py" ]; then
    DIR="$script_dir"
  else
    DIR="${DOCQA_DIR:-$HOME/docqa}"
  fi
  if [ -d "$DIR/.git" ]; then
    if [ "${DOCQA_NO_UPDATE:-0}" = 1 ]; then
      ok "đã có tại $DIR (bỏ qua cập nhật vì DOCQA_NO_UPDATE=1)"
    elif [ "$DRY_RUN" = 1 ]; then
      dry "git -C $DIR pull --ff-only"
    elif git -C "$DIR" pull --ff-only --quiet; then
      ok "đã có tại $DIR, đã cập nhật bản mới nhất"
    else
      warn "không cập nhật được (mất mạng hoặc có thay đổi cục bộ) – tiếp tục với bản hiện có tại $DIR"
    fi
  elif [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
    die "$DIR đã tồn tại nhưng không phải repo git của docqa. Đặt DOCQA_DIR sang thư mục khác rồi chạy lại."
  else
    require_net "tải mã nguồn"
    require_disk "$(dirname "$DIR")" "$DISK_APP_MB" "mã nguồn và thư viện Python"
    run git clone --quiet --branch "$DOCQA_BRANCH" "$DOCQA_REPO" "$DIR"
    did "đã tải về $DIR"
  fi
  if [ "$DRY_RUN" != 1 ] && [ ! -f "$DIR/app/launcher.py" ]; then
    die "$DIR không chứa mã nguồn docqa (thiếu app/launcher.py)."
  fi

  # ---------------------------------------------------------------- 4. venv + thư viện + .env + data/
  step "4/6 Môi trường Python (.venv) và thư viện"
  local VPY="$DIR/.venv/bin/python"
  if [ -x "$VPY" ] && "$VPY" -c 'import pip' >/dev/null 2>&1; then
    ok ".venv đã có"
  elif [ -e "$DIR/.venv" ]; then
    warn ".venv bị hỏng – tạo lại"
    run "$PY" -m venv --clear "$DIR/.venv"
  else
    require_disk "$DIR" "$DISK_APP_MB" "thư viện Python"
    run "$PY" -m venv "$DIR/.venv"
    did "đã tạo .venv"
  fi
  if [ "$DRY_RUN" = 1 ]; then
    dry "$VPY scripts/ensure_deps.py   (chỉ cài khi requirements.txt thay đổi)"
  else
    # ensure_deps.py tự bỏ qua nếu requirements.txt không đổi kể từ lần cài trước
    "$VPY" "$DIR/scripts/ensure_deps.py" || die "Cài thư viện Python thất bại (kiểm tra mạng và dung lượng đĩa)."
    ok "thư viện Python đã đủ"
  fi
  if [ -f "$DIR/.env" ]; then
    ok ".env đã có"
  else
    run cp "$DIR/.env.example" "$DIR/.env"
    did "đã tạo .env từ .env.example"
  fi
  if [ -d "$DIR/data" ]; then ok "data/ đã có"; else run mkdir -p "$DIR/data"; did "đã tạo data/"; fi
  local key
  for key in DOCQA_LLM_MODEL DOCQA_EMBED_MODEL DOCQA_OLLAMA_URL; do
    if [ -n "${!key:-}" ]; then
      if [ "$DRY_RUN" = 1 ]; then dry "ghi $key=${!key} vào .env"
      else "$VPY" "$DIR/scripts/install_helper.py" set-env "$key" "${!key}"; ok "đã ghi $key=${!key} vào .env"; fi
    fi
  done
  # Máy RAM < 12 GB: dùng qwen2.5:3b thay qwen3.5:4b – chỉ khi người dùng chưa tự chọn model
  # (DOCQA_MODEL, .env hoặc config.yaml). Tắt bằng DOCQA_AUTO_MODEL=0.
  if [ -z "${DOCQA_LLM_MODEL:-}" ] && [ "${DOCQA_AUTO_MODEL:-1}" != 0 ]; then
    if [ -x "$VPY" ] && [ -f "$DIR/scripts/install_helper.py" ]; then
      local auto_out auto_rc=0 auto_flag=""
      [ "$DRY_RUN" = 1 ] && auto_flag="--dry-run"
      auto_out="$("$VPY" "$DIR/scripts/install_helper.py" auto-llm ${auto_flag:+"$auto_flag"})" || auto_rc=$?
      [ -n "$auto_out" ] && say "$auto_out"
      # dry-run không ghi .env, nên đặt tạm biến môi trường để các bước sau hiện đúng model sẽ dùng
      if [ "$auto_rc" = 10 ] && [ "$DRY_RUN" = 1 ]; then export DOCQA_LLM_MODEL=qwen2.5:3b; fi
    elif [ "$DRY_RUN" = 1 ]; then
      dry "kiểm tra RAM: dưới 12 GB thì dùng qwen2.5:3b thay qwen3.5:4b"
    fi
  fi

  # Đọc cấu hình giống hệt app: config.yaml < .env < biến môi trường
  local EMBED LLM OLLAMA_URL PORT cfg
  if [ -x "$VPY" ] && cfg="$("$VPY" "$DIR/scripts/install_helper.py" config 2>/dev/null)"; then
    EMBED="$(printf '%s\n' "$cfg" | sed -n 1p)"
    LLM="$(printf '%s\n' "$cfg" | sed -n 2p)"
    OLLAMA_URL="$(printf '%s\n' "$cfg" | sed -n 3p)"
    PORT="$(printf '%s\n' "$cfg" | sed -n 4p)"
  else
    [ "$DRY_RUN" = 1 ] || die "Không đọc được cấu hình (config.yaml)."
    EMBED="${DOCQA_EMBED_MODEL:-$(yaml_get embed_model "$DIR/config.yaml")}"
    LLM="${DOCQA_LLM_MODEL:-$(yaml_get llm_model "$DIR/config.yaml")}"
    OLLAMA_URL="${DOCQA_OLLAMA_URL:-$(yaml_get ollama_url "$DIR/config.yaml")}"
    PORT="$(yaml_get port "$DIR/config.yaml")"
    EMBED="${EMBED:-<embed_model trong config.yaml>}" LLM="${LLM:-<llm_model trong config.yaml>}" OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}" PORT="${PORT:-8000}"
  fi
  say "Model cần dùng: embedding = $EMBED, LLM = $LLM; Ollama = $OLLAMA_URL"

  # ---------------------------------------------------------------- 5. Ollama
  step "5/6 Ollama (runtime chạy model)"
  local host_port="${OLLAMA_URL#*://}"; host_port="${host_port%%/*}"
  local host="${host_port%:*}" OLLAMA_BIN="" LOCAL=1
  case "$host" in localhost|127.0.0.1|0.0.0.0|"[::1]"|"") ;; *) LOCAL=0 ;; esac
  export OLLAMA_HOST="$host_port"

  if [ "$LOCAL" = 0 ]; then
    say "Ollama ở máy khác ($OLLAMA_URL) – không cài Ollama trên máy này."
    if [ "$DRY_RUN" = 1 ]; then dry "kiểm tra $OLLAMA_URL có phản hồi"
    else helper wait 15 || die "Không kết nối được Ollama tại $OLLAMA_URL."; ok "Ollama tại $OLLAMA_URL đang chạy"; fi
  else
    local wait_up=2
    OLLAMA_BIN="$(find_ollama)"
    if [ -n "$OLLAMA_BIN" ]; then
      ok "đã cài: $("$OLLAMA_BIN" --version 2>/dev/null | tail -n1 || echo "$OLLAMA_BIN")"
    else
      require_net "tải Ollama"
      require_disk "$HOME" "$DISK_OLLAMA_MB" "Ollama"
      install_ollama
      hash -r
      OLLAMA_BIN="$(find_ollama)"
      if [ -z "$OLLAMA_BIN" ]; then
        if [ "$DRY_RUN" = 1 ]; then OLLAMA_BIN=ollama
        else die "Đã cài Ollama nhưng không tìm thấy lệnh 'ollama'. Mở cửa sổ terminal mới rồi chạy lại."; fi
      fi
      did "đã cài Ollama"
      wait_up=15   # script cài vừa tự bật Ollama, cho nó vài giây trước khi tự khởi động thêm
    fi
    if ollama_up "$wait_up"; then
      ok "Ollama đang chạy"
    else
      start_ollama
      if [ "$DRY_RUN" = 1 ]; then :
      elif helper wait 60; then ok "Ollama đã chạy"
      else die "Ollama không khởi động được trong 60 giây. Xem log: $DIR/data/ollama.log (hoặc: journalctl -u ollama)"
      fi
    fi
  fi

  # ---------------------------------------------------------------- 6. model
  step "6/6 Model ($EMBED, $LLM)"
  local missing="" rc=0
  if [ "${EMBED#<}" != "$EMBED" ]; then
    say "(dry-run) chưa có mã nguồn nên chưa biết tên model; khi cài thật sẽ tải các model còn thiếu theo config.yaml."
  elif ollama_up && ! have_helper; then
    missing="$(missing_models_nohelper)" || rc=$?
    [ "$rc" = 0 ] || die "Không hỏi được danh sách model từ Ollama ($OLLAMA_URL)."
  elif ollama_up; then
    missing="$(helper missing)" || rc=$?
    [ "$rc" = 0 ] || die "Không hỏi được danh sách model từ Ollama ($OLLAMA_URL)."
  elif [ "$DRY_RUN" = 1 ]; then
    missing="$EMBED
$LLM"
    say "(dry-run) Ollama chưa chạy nên không kiểm tra được model đã có hay chưa."
  fi
  if [ "${EMBED#<}" != "$EMBED" ]; then :
  elif [ -z "$missing" ]; then
    ok "đã có đủ model, không cần tải"
  else
    require_net "tải model"
    [ "$LOCAL" = 1 ] && require_disk "$(models_dir)" "$DISK_MODELS_MB" "model"
    local m
    while IFS= read -r m; do
      [ -n "$m" ] || continue
      say "Đang tải model $m (chỉ lần đầu, có thể mất vài phút)..."
      if [ "$DRY_RUN" = 1 ]; then dry "ollama pull $m"
      elif [ -n "$OLLAMA_BIN" ]; then "$OLLAMA_BIN" pull "$m" || die "Tải model $m thất bại. Kiểm tra mạng / dung lượng rồi chạy lại (model đã tải dở sẽ được tải tiếp)."
      else helper pull "$m" || die "Tải model $m thất bại."
      fi
      [ "$DRY_RUN" = 1 ] || ok "đã tải $m"
    done <<EOF
$missing
EOF
    if [ "$DRY_RUN" != 1 ] && [ -n "$(helper missing)" ]; then
      die "Vẫn thiếu model: $(helper missing | tr '\n' ' ')"
    fi
  fi

  # ---------------------------------------------------------------- xong
  printf '\n'
  if [ "$DRY_RUN" = 1 ]; then
    say "Dry-run xong – chưa thay đổi gì. Bỏ --dry-run để cài thật."
  else
    say "Cài đặt xong! Chạy docqa:"
    printf '\n    cd %q && ./run.sh\n\n' "$DIR"
    say "Trình duyệt sẽ mở http://localhost:$PORT  (thêm --no-browser nếu không muốn tự mở)."
    say "Đổi model: sửa llm_model trong $DIR/config.yaml (hoặc DOCQA_LLM_MODEL trong .env) rồi chạy lại install.sh."
  fi
}

# ==================================================================== tiện ích
say()  { printf '[docqa] %s\n' "$*"; }
step() { printf '\n[docqa] == %s ==\n' "$*"; }
ok()   { printf '  [ok] %s\n' "$*"; }
did()  { [ "${DRY_RUN:-0}" = 1 ] || ok "$@"; }   # chỉ báo "đã làm" khi thật sự làm
warn() { printf '  [!] %s\n' "$*" >&2; }
dry()  { printf '  [dry-run] sẽ chạy: %s\n' "$*"; }
die()  { printf '\n[docqa] LỖI: %s\n' "$*" >&2; exit 1; }

# run CMD...: chạy lệnh, hoặc chỉ in ra khi dry-run
run() {
  if [ "${DRY_RUN:-0}" = 1 ]; then dry "$*"; else "$@"; fi
}

# as_root CMD...: chạy với quyền root (dùng sudo chỉ khi cần)
as_root() {
  if [ "$(id -u)" = 0 ]; then
    run "$@"
  elif command -v sudo >/dev/null 2>&1; then
    [ "${DRY_RUN:-0}" = 1 ] || say "Cần quyền quản trị để chạy: $* (sudo có thể hỏi mật khẩu)"
    run sudo "$@"
  else
    die "Cần quyền root để chạy '$*' nhưng không có sudo. Chạy lại với tài khoản root hoặc cài sudo."
  fi
}

APT_UPDATED=0
pkg_install() {
  say "Cài gói: $*"
  case "$PM" in
    apt-get)
      if [ "$APT_UPDATED" = 0 ]; then
        as_root apt-get update -qq || die "apt-get update thất bại (kiểm tra mạng hoặc nguồn apt)."
        APT_UPDATED=1
      fi
      # stdout của dpkg rất dài nên bỏ đi; lỗi vẫn in ra stderr
      as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@" >/dev/null || die "Cài $* bằng apt thất bại." ;;
    dnf) as_root dnf install -y -q "$@" || die "Cài $* bằng dnf thất bại." ;;
    pacman) as_root pacman -S --needed --noconfirm "$@" || die "Cài $* bằng pacman thất bại (thử 'sudo pacman -Syu' trước)." ;;
    brew) run brew install "$@" || die "Cài $* bằng Homebrew thất bại." ;;
    *) die "Không nhận ra trình quản lý gói (apt, dnf, pacman, brew). Hãy tự cài: $*" ;;
  esac
}

# In tên lệnh python đầu tiên có phiên bản >= 3.10 và dùng được venv (Debian/Ubuntu cần python3-venv)
find_python() {
  local c
  for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$c" >/dev/null 2>&1 &&
       "$c" -c 'import sys, venv, ensurepip; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
      printf '%s\n' "$c"; return 0
    fi
  done
  printf ''
}

NET_OK=""
check_net() {
  if [ -n "$NET_OK" ]; then return 0; fi
  if ! command -v curl >/dev/null 2>&1; then
    [ "${DRY_RUN:-0}" = 1 ] && say "(dry-run) chưa có curl nên chưa kiểm tra được kết nối mạng"
    return 0
  fi
  if curl -fsS -o /dev/null --max-time 10 https://github.com 2>/dev/null; then
    NET_OK=1
  else
    NET_OK=0
    warn "không kết nối được internet – chỉ dùng được những thứ đã cài sẵn"
  fi
}
require_net() {
  check_net
  [ "$NET_OK" = 1 ] || [ "${DRY_RUN:-0}" = 1 ] || die "Không có kết nối mạng (cần để $1). Kiểm tra mạng / proxy rồi chạy lại."
}

# require_disk PATH MB LÝ_DO: báo lỗi nếu ổ chứa PATH còn trống ít hơn MB
require_disk() {
  local p="$1" need_mb="$2" free
  while [ ! -d "$p" ]; do p="$(dirname "$p")"; done
  free="$(df -Pk "$p" 2>/dev/null | awk 'NR==2 {print int($4/1024)}')"
  [ -n "$free" ] || return 0
  if [ "$free" -lt "$need_mb" ]; then
    [ "${DRY_RUN:-0}" = 1 ] && { warn "ổ chứa $p chỉ còn ${free} MB, cần khoảng ${need_mb} MB cho $3"; return 0; }
    die "Không đủ dung lượng đĩa cho $3: ổ chứa $p còn ${free} MB, cần khoảng ${need_mb} MB."
  fi
}

# yaml_get KEY FILE: đọc giá trị đơn giản "key: value  # chú thích" (chỉ dùng khi dry-run chưa có .venv)
yaml_get() {
  [ -f "$2" ] || return 0
  sed -n "s/^$1:[[:space:]]*\([^[:space:]#]*\).*/\1/p" "$2" | head -n1
}

helper() { "$VPY" "$DIR/scripts/install_helper.py" "$@"; }

have_helper() { [ -x "${VPY:-}" ] && [ -f "$DIR/scripts/install_helper.py" ]; }

# ollama_up [GIÂY]: Ollama có phản hồi trong khoảng thời gian này không (mặc định 2 giây)
ollama_up() {
  if have_helper; then helper wait "${1:-2}" >/dev/null 2>&1
  else command -v curl >/dev/null 2>&1 && curl -fsS --max-time 2 -o /dev/null "${OLLAMA_URL%/}/api/tags"
  fi
}

# Chỉ dùng khi dry-run chưa có .venv: so tên model với /api/tags bằng grep (không cần python)
missing_models_nohelper() {
  local tags m
  tags="$(curl -fsS --max-time 5 "${OLLAMA_URL%/}/api/tags" | tr ',' '\n')" || return 3
  for m in "$EMBED" "$LLM"; do
    case "$m" in *:*) ;; *) printf '%s\n' "$tags" | grep -q "\"name\": *\"$m:latest\"" && continue ;; esac
    printf '%s\n' "$tags" | grep -q "\"name\": *\"$m\"" || printf '%s\n' "$m"
  done
}

find_ollama() {
  local c
  for c in "$(command -v ollama 2>/dev/null || true)" /usr/local/bin/ollama /usr/bin/ollama \
           /opt/homebrew/bin/ollama /Applications/Ollama.app/Contents/Resources/ollama \
           "$HOME/Applications/Ollama.app/Contents/Resources/ollama"; do
    if [ -n "$c" ] && [ -x "$c" ]; then printf '%s\n' "$c"; return 0; fi
  done
  printf ''
}

install_ollama() {
  if [ "$OS" = linux ]; then
    # script chính thức của Ollama cần zstd để giải nén (bản cài tối giản thường không có)
    command -v zstd >/dev/null 2>&1 || pkg_install zstd
    say "Cài Ollama bằng script chính thức https://ollama.com/install.sh (script tự dùng sudo khi cần)"
    if [ "${DRY_RUN:-0}" = 1 ]; then dry "curl -fsSL https://ollama.com/install.sh | sh"
    else curl -fsSL https://ollama.com/install.sh | sh || die "Cài Ollama thất bại. Xem hướng dẫn: https://ollama.com/download/linux"
    fi
  elif [ "$PM" = brew ]; then
    pkg_install ollama
  else
    # Không có Homebrew: tải app chính thức, giải nén trong thư mục tạm rồi chép vào Applications
    local dest=/Applications tmp
    [ -w "$dest" ] || dest="$HOME/Applications"
    say "Tải Ollama.app từ https://ollama.com/download/Ollama-darwin.zip vào $dest"
    if [ "${DRY_RUN:-0}" = 1 ]; then dry "curl -fL https://ollama.com/download/Ollama-darwin.zip; ditto -x -k ...; mv Ollama.app $dest/"; return 0; fi
    tmp="$(mktemp -d)"
    curl -fL --progress-bar -o "$tmp/Ollama.zip" https://ollama.com/download/Ollama-darwin.zip || { rm -rf "$tmp"; die "Tải Ollama thất bại."; }
    ditto -x -k "$tmp/Ollama.zip" "$tmp" || { rm -rf "$tmp"; die "Giải nén Ollama thất bại."; }
    mkdir -p "$dest"
    mv "$tmp/Ollama.app" "$dest/" || { rm -rf "$tmp"; die "Không chép được Ollama.app vào $dest."; }
    rm -rf "$tmp"
  fi
}

start_ollama() {
  local app=""
  [ -d /Applications/Ollama.app ] && app=/Applications/Ollama.app
  [ -z "$app" ] && [ -d "$HOME/Applications/Ollama.app" ] && app="$HOME/Applications/Ollama.app"
  if [ "$OS" = mac ] && [ -n "$app" ]; then
    say "Mở ứng dụng Ollama..."
    run open "$app"
  elif [ "$OS" = linux ] && [ -d /run/systemd/system ] && systemctl cat ollama.service >/dev/null 2>&1; then
    say "Khởi động dịch vụ ollama (systemd)..."
    as_root systemctl start ollama
  else
    say "Khởi động 'ollama serve' ở chế độ nền (log: $DIR/data/ollama.log)..."
    if [ "${DRY_RUN:-0}" = 1 ]; then dry "nohup $OLLAMA_BIN serve >> $DIR/data/ollama.log 2>&1 &"
    else mkdir -p "$DIR/data"; nohup "$OLLAMA_BIN" serve >>"$DIR/data/ollama.log" 2>&1 </dev/null &
    fi
  fi
}

# Thư mục Ollama lưu model (để kiểm tra dung lượng)
models_dir() {
  if [ -n "${OLLAMA_MODELS:-}" ]; then printf '%s\n' "$OLLAMA_MODELS"
  elif [ "$OS" = linux ] && [ -d /usr/share/ollama ]; then printf '%s\n' /usr/share/ollama
  else printf '%s\n' "$HOME"
  fi
}

trap 'die "lệnh ở dòng $LINENO thất bại (mã $?)."' ERR

main "$@"
