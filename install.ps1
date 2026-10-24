# docqa – cài đặt bằng một dòng trên Windows 10 (1809+) / 11, Windows PowerShell 5.1 trở lên.
#
#   PowerShell:  irm https://raw.githubusercontent.com/tuanpq-dev/docqa/main/install.ps1 | iex
#   cmd:         powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/tuanpq-dev/docqa/main/install.ps1 | iex"
#
#   Chỉ in ra những việc sẽ làm, không cài gì:
#     $env:DOCQA_DRYRUN=1; irm https://raw.githubusercontent.com/tuanpq-dev/docqa/main/install.ps1 | iex
#     powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -DryRun      (file đã tải về)
#
# Tự kiểm tra và chỉ cài những gì còn thiếu: Python >= 3.10 (winget), mã nguồn (zip từ GitHub, không cần git),
# .venv + thư viện, Ollama (winget) và các model trong config.yaml. Chạy lại bao nhiêu lần cũng được.
#
# Biến môi trường:
#   DOCQA_DIR             thư mục cài (mặc định %USERPROFILE%\docqa; chạy file nằm trong repo thì dùng chính repo đó)
#   DOCQA_MODEL           model LLM (ghi vào .env thành DOCQA_LLM_MODEL – tên khóa app đọc)
#   DOCQA_EMBED_MODEL     model embedding (ghi vào .env)
#   DOCQA_OLLAMA_URL      dùng Ollama ở máy khác (khi đó không cài Ollama trên máy này)
#   DOCQA_NO_UPDATE=1     không tải lại mã nguồn khi đã cài
#   DOCQA_REPO, DOCQA_BRANCH   cài từ fork / nhánh khác ("chu/repo" hoặc URL GitHub; mặc định tuanpq-dev/docqa, main)
#   DOCQA_ZIP_URL         đường dẫn/URL file zip mã nguồn (dùng để test)
#   DOCQA_DRYRUN=1        giống -DryRun
#
# Lưu ý mã hóa: file lưu UTF-8 KHÔNG có BOM (BOM làm hỏng "irm | iex"), nên Windows PowerShell 5.1 chạy
# bằng -File sẽ đọc file theo bảng mã ANSI. Vì vậy mọi chuỗi in ra màn hình chỉ dùng ASCII (tiếng Việt
# không dấu, giống run.bat); chỉ chú thích mới có dấu.
# Write-Host là chủ ý (đây là giao diện của trình cài); không BOM là chủ ý (xem "Lưu ý mã hóa" ở trên)
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '')]
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseBOMForUnicodeEncodedFile', '')]
param([switch]$DryRun)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$script:Dry = $DryRun.IsPresent -or ($env:DOCQA_DRYRUN -eq '1') -or ($env:DOCQA_DRY_RUN -eq '1')
$script:NetOk = $null
$script:HaveWinget = [bool](Get-Command winget -ErrorAction SilentlyContinue)
$PyMin = [version]'3.10'
$OllamaWaitSec = 30
# Dung lượng cần (MB), có dư: .venv + thư viện ~600 MB; Ollama ~2 GB; bge-m3 ~1,2 GB + qwen3.5:4b ~3,3 GB
$DiskAppMB = 1500
$DiskOllamaMB = 2500
$DiskModelsMB = 6000

# ==================================================================== tiện ích in
function Say([string]$m)  { Write-Host "[docqa] $m" }
function Step([string]$m) { Write-Host ''; Write-Host "[docqa] == $m ==" -ForegroundColor Cyan }
function Ok([string]$m)   { Write-Host "  [ok] $m" -ForegroundColor Green }
function Did([string]$m)  { if (-not $script:Dry) { Ok $m } }   # chỉ báo "đã làm" khi thật sự làm
function Warn([string]$m) { Write-Host "  [!] $m" -ForegroundColor Yellow }
function Dry([string]$m)  { Write-Host "  [dry-run] se lam: $m" -ForegroundColor DarkGray }
function Fail([string]$m) { throw [System.Exception]::new("DOCQA: $m") }

# Gọi chương trình ngoài, trả về mã thoát. -Quiet: bỏ hết output; -Capture: trả về @{Code; Out}.
# ErrorActionPreference tạm để Continue: trên PowerShell 5.1, chuyển hướng stderr của lệnh ngoài khi
# đang Stop sẽ bị coi là lỗi dù lệnh chạy thành công.
function Invoke-Exe {
  param([string]$Exe, [string[]]$ArgList = @(), [switch]$Quiet, [switch]$Capture)
  $old = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    if ($Capture) {
      $out = & $Exe @ArgList 2>$null
      return @{ Code = $LASTEXITCODE; Out = (@($out) -join "`n") }
    } elseif ($Quiet) {
      & $Exe @ArgList *> $null
    } else {
      & $Exe @ArgList | Out-Host   # in ra màn hình, không lẫn vào giá trị trả về
    }
    return $LASTEXITCODE
  } catch {
    if ($Capture) { return @{ Code = 1; Out = '' } }
    return 1
  } finally {
    $ErrorActionPreference = $old
  }
}

# Chạy lệnh thật (báo lỗi nếu mã thoát khác 0), hoặc chỉ in ra khi dry-run
function Run {
  param([string]$Exe, [string[]]$ArgList = @(), [string]$OnError)
  if ($script:Dry) { Dry "$Exe $($ArgList -join ' ')"; return }
  $code = Invoke-Exe $Exe $ArgList
  if ($code -ne 0) { Fail "$OnError (ma loi $code)" }
}

function Sync-PathFromRegistry {
  $m = [Environment]::GetEnvironmentVariable('Path', 'Machine')
  $u = [Environment]::GetEnvironmentVariable('Path', 'User')
  $env:Path = (@($m, $u) | Where-Object { $_ }) -join ';'
}

# ==================================================================== kiểm tra môi trường
function Test-Net {
  if ($null -ne $script:NetOk) { return $script:NetOk }
  try {
    Invoke-WebRequest -Uri 'https://github.com' -Method Head -UseBasicParsing -TimeoutSec 10 | Out-Null
    $script:NetOk = $true
  } catch { $script:NetOk = $false }
  return $script:NetOk
}
function Assert-Net([string]$why) {
  if (-not (Test-Net) -and -not $script:Dry) {
    Fail "Khong co ket noi mang (can de $why). Kiem tra mang / proxy / tuong lua roi chay lai."
  }
}

function Get-FreeMB([string]$path) {
  try {
    $root = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($path))
    return [int]([IO.DriveInfo]::new($root).AvailableFreeSpace / 1MB)
  } catch { return $null }
}
function Get-DriveRoot([string]$path) { return [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($path)) }

function Install-Winget([string]$id, [string]$name, [string]$manual) {
  if (-not $script:HaveWinget) {
    Fail "Thieu $name va may khong co winget. Cai 'App Installer' tu Microsoft Store (https://aka.ms/getwinget) roi chay lai, hoac tu cai $name tai $manual"
  }
  Assert-Net "tai $name"
  Say "Cai $name bang winget ($id). Neu Windows hoi quyen (UAC), hay chon Yes..."
  if ($script:Dry) { Dry "winget install --id $id -e --source winget"; return }
  $code = Invoke-Exe winget @('install', '--id', $id, '-e', '--source', 'winget', '--silent',
                              '--accept-package-agreements', '--accept-source-agreements')
  Sync-PathFromRegistry
  # -1978335189 = APPINSTALLER_CLI_ERROR_UPDATE_NOT_APPLICABLE: đã có bản mới nhất
  if ($code -ne 0 -and $code -ne -1978335189) {
    Fail "Cai $name bang winget that bai (ma loi $code). Thu tu cai tai $manual roi chay lai."
  }
}

# Đường dẫn python.exe >= 3.10 dùng được venv, hoặc $null. Bỏ qua bản "stub" của Microsoft Store
# (C:\Users\...\AppData\Local\Microsoft\WindowsApps\python.exe – chỉ mở Store chứ không chạy được).
function Find-Python {
  $check = "import sys, venv; print(sys.executable) if sys.version_info >= ($($PyMin.Major), $($PyMin.Minor)) else sys.exit(1)"
  $cands = @()
  if (Get-Command py -ErrorAction SilentlyContinue) { $cands += ,@('py', '-3') }
  foreach ($n in 'python', 'python3') {
    foreach ($c in @(Get-Command $n -All -ErrorAction SilentlyContinue)) {
      if ($c.Source -notlike '*\WindowsApps\*') { $cands += ,@($c.Source) }
    }
  }
  foreach ($exe in Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending) {
    $cands += ,@($exe.FullName)
  }
  foreach ($c in $cands) {
    $extra = @()
    if ($c.Count -gt 1) { $extra = $c[1..($c.Count - 1)] }
    $r = Invoke-Exe $c[0] ($extra + @('-c', $check)) -Capture
    $p = $r.Out.Trim()
    if ($r.Code -eq 0 -and $p -and $p -notlike '*\WindowsApps\*' -and (Test-Path $p)) { return $p }
  }
  return $null
}

function Find-Ollama {
  foreach ($c in @(Get-Command ollama -All -ErrorAction SilentlyContinue)) { if ($c.Source) { return $c.Source } }
  foreach ($p in "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe", "$env:ProgramFiles\Ollama\ollama.exe") {
    if (Test-Path $p) { return $p }
  }
  return $null
}

# Danh sách model Ollama đang có (tên ở cột NAME của "ollama list"), hoặc $null nếu Ollama không phản hồi.
# Không có lệnh ollama (Ollama ở máy khác) thì hỏi thẳng API /api/tags.
function Get-OllamaModelList([string]$ollama, [string]$url) {
  if ($ollama) {
    $r = Invoke-Exe $ollama @('list') -Capture
    if ($r.Code -ne 0) { return $null }
    $names = @($r.Out -split "`n" | Select-Object -Skip 1 | ForEach-Object { ($_.Trim() -split '\s+')[0] } | Where-Object { $_ })
    return ,$names
  }
  try {
    $t = Invoke-RestMethod -Uri ((Get-ApiUrl $url) + '/api/tags') -TimeoutSec 5 -UseBasicParsing
  } catch { return $null }
  return ,@(@($t.models) | Where-Object { $_ } | ForEach-Object { $_.name })
}

# PowerShell 5.1 thử IPv6 (::1) trước khi gọi "localhost", trong khi Ollama mặc định chỉ nghe 127.0.0.1
# -> mất vài giây và hay quá thời gian chờ. Gọi thẳng 127.0.0.1.
function Get-ApiUrl([string]$url) { return ($url.TrimEnd('/') -replace '^(https?://)localhost(?=[:/]|$)', '${1}127.0.0.1') }

# Ollama API có phản hồi không (dùng /api/version, nhanh và không cần model)
function Test-OllamaUp([string]$url, [int]$timeoutSec = 3) {
  try {
    Invoke-RestMethod -Uri ((Get-ApiUrl $url) + '/api/version') -TimeoutSec $timeoutSec -UseBasicParsing | Out-Null
    return $true
  } catch { return $false }
}
function Wait-Ollama([string]$url, [int]$seconds) {
  $deadline = (Get-Date).AddSeconds($seconds)
  do {
    if (Test-OllamaUp $url) { return $true }
    Start-Sleep -Seconds 1
  } while ((Get-Date) -lt $deadline)
  return $false
}

# Giống has_model() trong app/launcher.py: "bge-m3" khớp cả "bge-m3:latest"
function Test-HasModel([string[]]$names, [string]$model) {
  return ($names -contains $model) -or (($model -notlike '*:*') -and ($names -contains "${model}:latest"))
}

function Get-YamlValue([string]$key, [string]$file) {
  if (-not (Test-Path $file)) { return '' }
  foreach ($line in Get-Content $file -Encoding UTF8) {
    if ($line -match "^${key}:\s*([^\s#]+)") { return $Matches[1] }
  }
  return ''
}

# ==================================================================== mã nguồn (zip, không cần git)
# Mã phiên bản của bản mới nhất: SHA commit (GitHub API) hoặc SHA256 của file zip cục bộ; $null nếu không hỏi được.
function Get-RemoteVersion([string]$slug, [string]$branch, [string]$zipUrl) {
  if ($env:DOCQA_ZIP_URL) {
    if (Test-Path $zipUrl) { return (Get-FileHash $zipUrl -Algorithm SHA256).Hash }
    return $null
  }
  try {
    $r = Invoke-WebRequest -Uri "https://api.github.com/repos/$slug/commits/$branch" -UseBasicParsing -TimeoutSec 15 `
           -Headers @{ Accept = 'application/vnd.github.sha'; 'User-Agent' = 'docqa-install' }
    # PowerShell 5.1 trả Content dạng byte[] khi content-type không phải text
    $c = $r.Content
    if ($c -is [byte[]]) { $c = [Text.Encoding]::ASCII.GetString($c) }
    $sha = ([string]$c).Trim()
    if ($sha -match '^[0-9a-f]{40}$') { return $sha }
  } catch { $sha = $null }   # không hỏi được (mất mạng, bị giới hạn lượt gọi API): trả $null
  return $null
}

# Tải zip vào thư mục tạm riêng, giải nén, chép đè mã nguồn vào $dir. Không đụng tới .env, data\, .venv
# (zip từ GitHub vốn không chứa chúng; vẫn loại ra cho chắc). Thư mục tạm luôn được xóa.
function Install-Source([string]$dir, [string]$zipUrl, [string]$version) {
  $tmp = Join-Path ([IO.Path]::GetTempPath()) ('docqa-install-' + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $tmp | Out-Null
  try {
    $zip = Join-Path $tmp 'docqa.zip'
    if (Test-Path $zipUrl) { Copy-Item $zipUrl $zip }
    else {
      $old = $ProgressPreference; $ProgressPreference = 'SilentlyContinue'   # thanh tiến độ làm IWR chậm hẳn trên PS 5.1
      try { Invoke-WebRequest -Uri $zipUrl -OutFile $zip -UseBasicParsing -TimeoutSec 300 }
      catch { Fail "Tai ma nguon that bai ($zipUrl): $($_.Exception.Message)" }
      finally { $ProgressPreference = $old }
    }
    $ex = Join-Path $tmp 'x'
    try { Expand-Archive -Path $zip -DestinationPath $ex -Force }
    catch { Fail "File zip ma nguon bi hong, khong giai nen duoc. Chay lai de tai lai." }
    # zip của GitHub có đúng một thư mục gốc dạng docqa-main\
    $root = @(Get-ChildItem $ex -Directory)
    $src = if ($root.Count -eq 1 -and -not (Test-Path (Join-Path $ex 'app'))) { $root[0].FullName } else { $ex }
    if (-not (Test-Path (Join-Path $src 'app\launcher.py'))) { Fail 'File zip khong chua ma nguon docqa (thieu app\launcher.py).' }
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    Get-ChildItem $src -Force | Where-Object { @('.env', 'data', '.venv', '.git') -notcontains $_.Name } |
      Copy-Item -Destination $dir -Recurse -Force
    if ($version) { Set-Content -Path (Join-Path $dir '.docqa-version') -Value $version -Encoding ASCII }
  } finally {
    Remove-Item -Recurse -Force -LiteralPath $tmp -ErrorAction SilentlyContinue
  }
}

# ==================================================================== chính
function Install-Docqa {
  [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
  # DOCQA_REPO nhận cả "chu/repo" lẫn URL git đầy đủ (giống install.sh)
  $slug = if ($env:DOCQA_REPO) { $env:DOCQA_REPO -replace '^https?://github\.com/', '' -replace '\.git$', '' -replace '/$', '' } else { 'tuanpq-dev/docqa' }
  $branch = if ($env:DOCQA_BRANCH) { $env:DOCQA_BRANCH } else { 'main' }
  $zipUrl = if ($env:DOCQA_ZIP_URL) { $env:DOCQA_ZIP_URL } else { "https://github.com/$slug/archive/refs/heads/$branch.zip" }
  # DOCQA_MODEL là tên ngắn cho người dùng; app đọc DOCQA_LLM_MODEL
  if ($env:DOCQA_MODEL) { $env:DOCQA_LLM_MODEL = $env:DOCQA_MODEL }

  if ($script:Dry) { Say 'CHE DO DRY-RUN: chi kiem tra va in ra cac viec se lam, khong cai gi.' }

  # Thư mục cài
  if (-not $env:DOCQA_DIR -and $PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot 'app\launcher.py'))) { $dir = $PSScriptRoot }
  elseif ($env:DOCQA_DIR) { $dir = $env:DOCQA_DIR }
  else { $dir = Join-Path $HOME 'docqa' }
  $dir = [IO.Path]::GetFullPath($dir)
  $venv = Join-Path $dir '.venv'
  $vpy = Join-Path $venv 'Scripts\python.exe'
  $helperPy = Join-Path $dir 'scripts\install_helper.py'
  $dataDir = Join-Path $dir 'data'
  $hasSource = Test-Path (Join-Path $dir 'app\launcher.py')

  # ------------------------------------------------------------ 0. kiểm tra trước khi cài
  Step '0/5 Kiem tra truoc khi cai'
  $osBuild = [Environment]::OSVersion.Version.Build
  if ($osBuild -lt 17763) { Warn "Windows build $osBuild cu hon Windows 10 1809 (17763): winget co the khong chay." }
  else { Ok "Windows build $osBuild" }
  if ($script:HaveWinget) { Ok 'co winget' } else { Warn 'khong co winget - chi cai duoc neu Python va Ollama da co san' }
  if (Test-Net) { Ok 'co ket noi internet' } else { Warn 'khong ket noi duoc internet - chi dung duoc nhung thu da co san' }

  $py = Find-Python
  $ollama = Find-Ollama
  $ollamaUrlGuess = if ($env:DOCQA_OLLAMA_URL) { $env:DOCQA_OLLAMA_URL } else { 'http://localhost:11434' }
  $localGuess = $ollamaUrlGuess -match '^[a-z]+://(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(:|/|$)'
  $needWinget = @()
  if (-not $py -and -not (Test-Path $vpy)) { $needWinget += 'Python' }
  if (-not $ollama -and $localGuess) { $needWinget += 'Ollama' }
  if ($needWinget.Count -gt 0 -and -not $script:HaveWinget) {
    Fail ("Can cai " + ($needWinget -join ', ') + " nhung may khong co winget.`n" +
          "         Cach 1: cai 'App Installer' tu Microsoft Store (https://aka.ms/getwinget) roi chay lai lenh cai.`n" +
          "         Cach 2: tu cai Python 3.12 (https://www.python.org/downloads/ - tick 'Add python.exe to PATH')`n" +
          "                 va Ollama (https://ollama.com/download/windows), mo cua so moi roi chay lai.")
  }
  # Dung lượng cần theo từng ổ đĩa (chỉ tính phần còn thiếu)
  $need = @{}
  $addNeed = { param($path, $mb) $root = Get-DriveRoot $path; if ($need.ContainsKey($root)) { $need[$root] += $mb } else { $need[$root] = $mb } }
  if (-not (Test-Path $vpy)) { & $addNeed $dir $DiskAppMB }
  if (-not $ollama -and $localGuess) { & $addNeed $env:LOCALAPPDATA $DiskOllamaMB }
  if ($localGuess) {
    $modelsDir = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { $HOME }
    $known = if ($ollama) { Get-OllamaModelList $ollama $ollamaUrlGuess } else { $null }
    $cfgEmbed = if ($env:DOCQA_EMBED_MODEL) { $env:DOCQA_EMBED_MODEL } else { Get-YamlValue 'embed_model' (Join-Path $dir 'config.yaml') }
    $cfgLlm = if ($env:DOCQA_LLM_MODEL) { $env:DOCQA_LLM_MODEL } else { Get-YamlValue 'llm_model' (Join-Path $dir 'config.yaml') }
    $allThere = ($null -ne $known) -and $cfgEmbed -and $cfgLlm -and (Test-HasModel $known $cfgEmbed) -and (Test-HasModel $known $cfgLlm)
    if (-not $allThere) { & $addNeed $modelsDir $DiskModelsMB }
  }
  foreach ($root in $need.Keys) {
    $free = Get-FreeMB $root
    if ($null -eq $free) { continue }
    if ($free -lt $need[$root]) {
      $msg = "Khong du dung luong: o $root con $free MB, can khoang $($need[$root]) MB (thu vien, Ollama va model). Don dep o dia hoac dat DOCQA_DIR / OLLAMA_MODELS sang o khac."
      if ($script:Dry) { Warn $msg } else { Fail $msg }
    } else { Ok "o $root con $free MB (can khoang $($need[$root]) MB)" }
  }

  # ------------------------------------------------------------ 1. Python
  Step "1/5 Python >= $PyMin"
  if ($py) { Ok "dung $py" }
  elseif (Test-Path $vpy) { Ok "khong thay Python he thong, nhung .venv da co san - bo qua" }
  else {
    Install-Winget -Id 'Python.Python.3.12' -Name 'Python 3.12' -Manual 'https://www.python.org/downloads/'
    $py = Find-Python
    if ($py) { Did "da cai Python: $py" }
    elseif ($script:Dry) { $py = 'python' }
    else { Fail "Da cai Python nhung chua tim thay. Dong cua so nay, mo cua so PowerShell moi roi chay lai lenh cai." }
  }

  # ------------------------------------------------------------ 2. mã nguồn
  Step '2/5 Ma nguon docqa'
  $stampFile = Join-Path $dir '.docqa-version'
  $isGit = Test-Path (Join-Path $dir '.git')
  if ($hasSource -and $env:DOCQA_NO_UPDATE -eq '1') {
    Ok "da co tai $dir (bo qua cap nhat vi DOCQA_NO_UPDATE=1)"
  } elseif ($hasSource -and $isGit) {
    # Bản clone bằng git (vd. nhà phát triển): cập nhật bằng git nếu có, không chép zip đè lên
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Ok "da co tai $dir (ban git; khong co git nen bo qua cap nhat)" }
    elseif ($script:Dry) { Dry "git -C $dir pull --ff-only" }
    elseif ((Invoke-Exe git @('-C', $dir, 'pull', '--ff-only', '--quiet')) -eq 0) { Ok "da co tai $dir (ban git), da cap nhat" }
    else { Warn "khong cap nhat duoc bang git (mat mang hoac co thay doi cuc bo) - dung ban hien co" }
  } elseif (-not $hasSource -and (Test-Path $dir) -and (Get-ChildItem -Force $dir | Select-Object -First 1)) {
    Fail "$dir da ton tai nhung khong phai thu muc docqa. Dat `$env:DOCQA_DIR sang thu muc khac roi chay lai."
  } else {
    $latest = if (Test-Net) { Get-RemoteVersion -Slug $slug -Branch $branch -ZipUrl $zipUrl } else { $null }
    $current = if (Test-Path $stampFile) { (Get-Content $stampFile -Raw).Trim() } else { $null }
    if ($hasSource -and $latest -and $current -eq $latest) {
      Ok "da co ban moi nhat tai $dir ($($latest.Substring(0, 7)))"
    } elseif ($hasSource -and -not (Test-Net)) {
      Warn "khong co mang - dung ban hien co tai $dir"
    } elseif ($hasSource -and -not $latest -and -not $env:DOCQA_ZIP_URL) {
      Warn "khong hoi duoc GitHub ban moi nhat la gi (co the bi gioi han tam thoi) - giu ban hien co tai $dir"
    } else {
      Assert-Net 'tai ma nguon'
      $what = if ($hasSource) { 'Cap nhat' } else { 'Tai' }
      Say "$what ma nguon tu $zipUrl (giu nguyen .env, data\, .venv)..."
      if ($script:Dry) { Dry "tai zip, giai nen vao thu muc tam roi chep vao $dir" }
      else { Install-Source -Dir $dir -ZipUrl $zipUrl -Version $latest; Ok "ma nguon da san sang tai $dir" }
    }
  }
  if (-not $script:Dry -and -not (Test-Path (Join-Path $dir 'app\launcher.py'))) { Fail "$dir khong chua ma nguon docqa (thieu app\launcher.py)." }

  # ------------------------------------------------------------ 3. venv + thư viện + .env + data
  Step '3/5 Moi truong Python (.venv) va thu vien'
  if ((Test-Path $vpy) -and (Invoke-Exe $vpy @('-c', 'import pip') -Quiet) -eq 0) { Ok '.venv da co' }
  elseif (Test-Path $venv) {
    Warn '.venv bi hong - tao lai'
    if (-not $py) { Fail "Can Python $PyMin+ de tao lai .venv. Cai Python roi chay lai." }
    Run $py @('-m', 'venv', '--clear', $venv) -OnError 'Khong tao lai duoc .venv'
  } else {
    Run $py @('-m', 'venv', $venv) -OnError 'Khong tao duoc .venv'
    Did 'da tao .venv'
  }
  # ensure_deps.py gọi "<.venv>\Scripts\python.exe -m pip install -r requirements.txt" và tự bỏ qua
  # nếu requirements.txt không đổi kể từ lần cài trước
  Run $vpy @((Join-Path $dir 'scripts\ensure_deps.py')) -OnError 'Cai thu vien Python that bai (kiem tra mang va dung luong dia)'
  Did 'thu vien Python da du'
  $envFile = Join-Path $dir '.env'
  if (Test-Path $envFile) { Ok '.env da co' }
  elseif ($script:Dry) { Dry 'chep .env.example thanh .env' }
  else { Copy-Item (Join-Path $dir '.env.example') $envFile; Ok 'da tao .env tu .env.example' }
  if (Test-Path $dataDir) { Ok 'data\ da co' }
  elseif ($script:Dry) { Dry "tao thu muc $dataDir" }
  else { New-Item -ItemType Directory -Path $dataDir | Out-Null; Ok 'da tao data\' }
  foreach ($key in 'DOCQA_LLM_MODEL', 'DOCQA_EMBED_MODEL', 'DOCQA_OLLAMA_URL') {
    $val = [Environment]::GetEnvironmentVariable($key)
    if ($val) {
      Run $vpy @($helperPy, 'set-env', $key, $val) -OnError "Khong ghi duoc $key vao .env"
      Did "da ghi $key=$val vao .env"
    }
  }
  # Máy RAM < 12 GB: dùng qwen2.5:3b thay qwen3.5:4b – chỉ khi người dùng chưa tự chọn model
  # (DOCQA_MODEL, .env hoặc config.yaml). Tắt bằng DOCQA_AUTO_MODEL=0.
  if (-not $env:DOCQA_LLM_MODEL -and $env:DOCQA_AUTO_MODEL -ne '0') {
    if ((Test-Path $vpy) -and (Test-Path $helperPy)) {
      $flags = @($helperPy, 'auto-llm'); if ($script:Dry) { $flags += '--dry-run' }
      $r = Invoke-Exe $vpy $flags -Capture
      if ($r.Out) { Say $r.Out.Trim() }
      # dry-run không ghi .env, nên đặt tạm biến môi trường để các bước sau hiện đúng model sẽ dùng
      if ($r.Code -eq 10 -and $script:Dry) { $env:DOCQA_LLM_MODEL = 'qwen2.5:3b' }
    } elseif ($script:Dry) { Dry 'kiem tra RAM: duoi 12 GB thi dung qwen2.5:3b thay qwen3.5:4b' }
  }

  # Đọc cấu hình giống hệt app: config.yaml < .env < biến môi trường DOCQA_*
  $cfg = if ((Test-Path $vpy) -and (Test-Path $helperPy)) { Invoke-Exe $vpy @($helperPy, 'config') -Capture } else { @{ Code = 1; Out = '' } }
  if ($cfg.Code -eq 0) {
    $embed, $llm, $ollamaUrl, $port = $cfg.Out -split "`n" | ForEach-Object { $_.Trim() }
  } elseif ($script:Dry) {
    $yaml = Join-Path $dir 'config.yaml'
    $embed = if ($env:DOCQA_EMBED_MODEL) { $env:DOCQA_EMBED_MODEL } else { Get-YamlValue 'embed_model' $yaml }
    $llm = if ($env:DOCQA_LLM_MODEL) { $env:DOCQA_LLM_MODEL } else { Get-YamlValue 'llm_model' $yaml }
    $ollamaUrl = $ollamaUrlGuess
    $port = Get-YamlValue 'port' $yaml
    if (-not $port) { $port = '8000' }
  } else {
    # Lấy dòng lỗi cuối của Python (vd. "ValueError: ... '8011  # ...'") thay vì cả traceback
    $why = ''
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $why = (@(& $vpy $helperPy 'config' 2>&1 | ForEach-Object { "$_".Trim() } | Where-Object { $_ }) | Select-Object -Last 1) }
    catch { $why = '' } finally { $ErrorActionPreference = $old }
    Fail "Khong doc duoc cau hinh. Kiem tra config.yaml va .env trong $dir (.env khong ho tro chu thich '#' cuoi dong).`n         Chi tiet: $why"
  }
  if ($embed -and $llm) { Say "Model can dung: embedding = $embed, LLM = $llm; Ollama = $ollamaUrl" }

  # ------------------------------------------------------------ 4. Ollama
  Step '4/5 Ollama (runtime chay model)'
  $hostPort = ($ollamaUrl -replace '^[a-zA-Z]+://', '') -replace '/.*$', ''
  $hostName = $hostPort -replace ':\d+$', ''
  $isLocal = @('localhost', '127.0.0.1', '0.0.0.0', '[::1]', '') -contains $hostName
  $env:OLLAMA_HOST = $hostPort   # để lệnh "ollama list/pull" nói chuyện đúng địa chỉ trong config
  if (-not $isLocal) {
    Say "Ollama o may khac ($ollamaUrl) - khong cai Ollama tren may nay."
    if ($script:Dry) { Dry "kiem tra $ollamaUrl co phan hoi" }
    elseif (-not (Wait-Ollama $ollamaUrl 15)) { Fail "Khong ket noi duoc Ollama tai $ollamaUrl." }
    else { Ok "Ollama tai $ollamaUrl dang chay" }
  } else {
    if ($ollama) {
      $ver = Invoke-Exe $ollama @('--version') -Capture
      Ok "da cai: $(($ver.Out -split "`n")[-1].Trim())"
    } else {
      Install-Winget -Id 'Ollama.Ollama' -Name 'Ollama' -Manual 'https://ollama.com/download/windows'
      $ollama = Find-Ollama
      if (-not $ollama) {
        if ($script:Dry) { $ollama = 'ollama' }
        else { Fail 'Da cai Ollama nhung chua tim thay ollama.exe. Dong cua so nay, mo cua so moi roi chay lai lenh cai.' }
      }
      Did 'da cai Ollama'
    }
    if (Test-OllamaUp $ollamaUrl) { Ok 'Ollama dang chay' }
    elseif ((-not $script:Dry) -and (Wait-Ollama $ollamaUrl 5)) { Ok 'Ollama dang chay' }   # trình cài winget có thể vừa tự bật Ollama
    elseif ($script:Dry) { Dry "chay an '$ollama serve' roi doi toi da $OllamaWaitSec giay" }
    else {
      $log = Join-Path $dataDir 'ollama.log'
      Say "Khoi dong Ollama o che do an (log: $log)..."
      Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $dataDir 'ollama.out.log') -RedirectStandardError $log | Out-Null
      if (-not (Wait-Ollama $ollamaUrl $OllamaWaitSec)) {
        Fail "Ollama khong khoi dong duoc sau $OllamaWaitSec giay. Xem log: $log - hoac mo 'Ollama' tu Start menu roi chay lai."
      }
      Ok 'Ollama da chay'
    }
  }

  # ------------------------------------------------------------ 5. model
  Step '5/5 Model'
  if (-not $embed -or -not $llm) {
    Say '(dry-run) chua co ma nguon nen chua biet ten model; khi cai that se tai cac model con thieu theo config.yaml.'
  } else {
    $cli = if ($ollama -and (Test-Path $ollama)) { $ollama } else { $null }
    $names = Get-OllamaModelList $cli $ollamaUrl
    if ($null -eq $names) {
      if (-not $script:Dry) { Fail "Khong lay duoc danh sach model (ollama list) tu $ollamaUrl." }
      Say '(dry-run) Ollama chua chay nen chua biet model nao da co.'
      $names = @()
    }
    foreach ($m in @($embed, $llm) | Select-Object -Unique) {
      if (Test-HasModel $names $m) { Ok "da co $m - bo qua"; continue }
      Assert-Net "tai model $m"
      Say "Tai model $m (chi lan dau, co the mat vai phut)..."
      if ($script:Dry) { Dry "ollama pull $m"; continue }
      $code = if ($cli) { Invoke-Exe $cli @('pull', $m) } else { Invoke-Exe $vpy @($helperPy, 'pull', $m) }
      if ($code -ne 0) { Fail "Tai model $m that bai. Kiem tra mang / dung luong roi chay lai (phan da tai se duoc tai tiep)." }
      Ok "da tai $m"
    }
  }

  # ------------------------------------------------------------ xong
  Write-Host ''
  if ($script:Dry) { Say 'Dry-run xong - chua cai gi. Bo -DryRun / DOCQA_DRYRUN de cai that.'; return }
  Say 'Cai dat xong! Moi lan muon dung docqa:'
  Write-Host ''
  Write-Host "    PowerShell:  & `"$dir\run.bat`"" -ForegroundColor Green
  Write-Host "    cmd:         `"$dir\run.bat`"" -ForegroundColor Green
  Write-Host "    hoac nhap dup run.bat trong thu muc $dir" -ForegroundColor Green
  Write-Host ''
  Say "Trinh duyet se mo http://localhost:$port  (nhan Ctrl+C trong cua so do de dung)."
  Say "Doi model: dat DOCQA_MODEL roi chay lai lenh cai, hoac sua llm_model trong $dir\config.yaml."
}

# Có được phép "exit" không: chạy bằng -File, hoặc là tiến trình powershell riêng chạy -Command (từ cmd).
# Khi người dùng dán "irm | iex" vào cửa sổ PowerShell đang mở thì KHÔNG exit, để khỏi đóng cửa sổ của họ.
function Test-OwnProcess {
  if ($PSCommandPath) { return $true }
  $a = @([Environment]::GetCommandLineArgs() | ForEach-Object { $_.ToLowerInvariant() })
  return (($a -contains '-command') -or ($a -contains '-c')) -and -not ($a -contains '-noexit')
}

# "irm | iex" chạy ngay trong phiên của người dùng: trả lại các biến môi trường script đã đặt tạm
$savedEnv = @{}
foreach ($k in 'OLLAMA_HOST', 'DOCQA_LLM_MODEL') { $savedEnv[$k] = [Environment]::GetEnvironmentVariable($k) }
try {
  Install-Docqa
} catch {
  $msg = $_.Exception.Message
  Write-Host ''
  if ($msg -like 'DOCQA: *') {
    Write-Host "[docqa] LOI: $($msg.Substring(7))" -ForegroundColor Red
  } else {
    # Lỗi ngoài dự kiến: một dòng mô tả + vị trí, không in stack trace dài
    Write-Host "[docqa] LOI khong mong muon: $msg" -ForegroundColor Red
    Write-Host "  (dong $($_.InvocationInfo.ScriptLineNumber) trong install.ps1 - hay gui dong nay khi bao loi)" -ForegroundColor DarkGray
  }
  if (Test-OwnProcess) { exit 1 }
} finally {
  foreach ($k in $savedEnv.Keys) { [Environment]::SetEnvironmentVariable($k, $savedEnv[$k]) }
}
