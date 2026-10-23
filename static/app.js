/* docqa – giao diện. JS thuần, không framework, không tải gì từ internet. */
"use strict";

(() => {
  // ================================================================ tiện ích
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
  const uid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  const nf = new Intl.NumberFormat("vi-VN");
  const isMobile = () => window.matchMedia("(max-width: 899.98px)").matches;
  const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const store = {
    get(k, d = null) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch { return false; } },
    del(k) { try { localStorage.removeItem(k); } catch { /* bỏ qua */ } },
  };

  function newSid() {
    if (window.crypto?.randomUUID) return crypto.randomUUID();
    const a = new Uint8Array(16); crypto.getRandomValues(a);
    return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
  }
  let SID = store.get("docqa:sid");
  if (!SID || !/^[A-Za-z0-9_-]{8,64}$/.test(SID)) { SID = newSid(); store.set("docqa:sid", SID); }
  const chatKey = () => "docqa:chat:" + SID;

  function fmtBytes(n) {
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return nf.format(Math.round(n / 1024)) + " KB";
    return (n / 1024 / 1024).toLocaleString("vi-VN", { maximumFractionDigits: 1 }) + " MB";
  }

  // ================================================================ trạng thái
  const state = {
    docs: [],
    stats: null,
    suggestions: { status: "none", items: [] },
    messages: store.get(chatKey(), []) || [],
    generating: null,
    activeMsgId: null,
    config: { max_upload_mb: 50, max_files: 5 },
    uploading: false,
    replaceId: null,
    healthOk: null,
  };
  // tin nhắn đang dở từ lần trước (tắt trang khi đang sinh) → đánh dấu đã dừng
  state.messages.forEach((m) => { if (m.pending) { m.pending = false; m.stopped = true; } });

  function saveChat() {
    const msgs = state.messages.slice(-100).map(({ pending, started, stageText, ...m }) => m);
    store.set(chatKey(), msgs);
  }

  // ================================================================ gọi API
  function api(path, opts = {}) {
    const headers = { "X-Session-Id": SID, ...(opts.headers || {}) };
    return fetch(path, { ...opts, headers });
  }
  async function apiJson(path, opts) {
    const r = await api(path, opts);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(j.message || j.detail || "HTTP " + r.status), { data: j, status: r.status });
    return j;
  }
  function parseSSEBlock(block) {
    let event = "message"; const data = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (!data.length) return null;
    try { return { event, data: JSON.parse(data.join("\n")) }; } catch { return null; }
  }
  async function* readSSE(resp) {
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const ev = parseSSEBlock(buf.slice(0, i));
        buf = buf.slice(i + 2);
        if (ev) yield ev;
      }
    }
    const ev = buf.trim() && parseSSEBlock(buf);
    if (ev) yield ev;
  }
  const SERVER_DOWN = { message: "Mất kết nối tới server docqa.", hint: "Kiểm tra cửa sổ terminal đang chạy docqa." };

  // ================================================================ toast & hộp xác nhận
  function toast(text, kind = "") {
    const t = document.createElement("div");
    t.className = "toast" + (kind ? " " + kind : "");
    t.textContent = text;
    $("#toasts").append(t);
    setTimeout(() => t.remove(), kind === "err" ? 4500 : 2600);
  }
  function confirmDialog(title, body, okLabel = "Xóa") {
    const d = $("#confirm-dialog");
    $("#confirm-title").textContent = title;
    $("#confirm-body").textContent = body;
    $("#confirm-ok").textContent = okLabel;
    return new Promise((resolve) => {
      d.returnValue = "";
      d.addEventListener("close", () => resolve(d.returnValue === "ok"), { once: true });
      d.showModal();
      $("button[value=cancel]", d).focus();
    });
  }

  // ================================================================ markdown an toàn
  // Luôn escape HTML trước, sau đó mới chèn thẻ do chính chúng ta tạo ra.
  function inlineMd(text, nSrc) {
    return text.split(/(`[^`\n]+`)/g).map((part, i) => {
      if (i % 2 === 1) return "<code>" + escapeHtml(part.slice(1, -1)) + "</code>";
      let h = escapeHtml(part);
      h = h.replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, "<strong>$1</strong>");
      h = h.replace(/__(?=\S)([\s\S]*?\S)__/g, "<strong>$1</strong>");
      h = h.replace(/(^|[^*\w])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?![*\w])/g, "$1<em>$2</em>");
      // [1], [1, 2], và các dạng model nhỏ hay viết: [số 1], [nguồn 2], [đoạn 3], [Source 1]
      h = h.replace(/\[(?:số|nguồn|đoạn|đoạn trích|source|excerpt)?\s*(\d{1,2}(?:\s*[,;]\s*\d{1,2})*)\]/gi, (m, list) =>
        list.split(/[,;]/).map((x) => parseInt(x, 10)).map((n) =>
          n >= 1 && n <= nSrc
            ? `<button type="button" class="cite" data-n="${n}" aria-label="Xem nguồn ${n}">${n}</button>`
            : `[${n}]`).join(""));
      return h;
    }).join("");
  }
  function mdBlocks(text, nSrc) {
    let html = "", para = [], list = null;
    const flushPara = () => { if (para.length) { html += "<p>" + para.map((l) => inlineMd(l, nSrc)).join("<br>") + "</p>"; para = []; } };
    const flushList = () => {
      if (!list) return;
      const start = list.type === "ol" && list.start > 1 ? ` start="${list.start}"` : "";
      html += `<${list.type}${start}>` + list.items.map((it) => "<li>" + inlineMd(it, nSrc) + "</li>").join("") + `</${list.type}>`;
      list = null;
    };
    for (const raw of text.split("\n")) {
      const line = raw.trimEnd();
      let m;
      if (!line.trim()) { flushPara(); flushList(); continue; }
      if ((m = line.match(/^\s{0,3}#{1,6}\s+(.*)$/))) { flushPara(); flushList(); html += "<h4>" + inlineMd(m[1], nSrc) + "</h4>"; continue; }
      if ((m = line.match(/^\s*[-*•+]\s+(.*)$/))) {
        flushPara(); if (!list || list.type !== "ul") { flushList(); list = { type: "ul", items: [] }; }
        list.items.push(m[1]); continue;
      }
      if ((m = line.match(/^\s*(\d{1,3})[.)]\s+(.*)$/))) {
        flushPara(); if (!list || list.type !== "ol") { flushList(); list = { type: "ol", items: [], start: parseInt(m[1], 10) }; }
        list.items.push(m[2]); continue;
      }
      if (list && /^\s{2,}\S/.test(raw)) { list.items[list.items.length - 1] += " " + line.trim(); continue; }
      flushList(); para.push(line);
    }
    flushPara(); flushList();
    return html;
  }
  function renderMarkdown(src, nSrc = 0) {
    const fence = /```[^\n]*\n?([\s\S]*?)(?:```|$)/g;
    let out = "", last = 0, m;
    while ((m = fence.exec(src)) !== null) {
      if (m[0] === "") break;
      out += mdBlocks(src.slice(last, m.index), nSrc);
      out += "<pre><code>" + escapeHtml(m[1].replace(/\n$/, "")) + "</code></pre>";
      last = fence.lastIndex;
    }
    return out + mdBlocks(src.slice(last), nSrc);
  }

  // Tô nhẹ từ khóa trùng với câu hỏi (an toàn: escape từng mảnh).
  const STOP = new Set(("và là của có các những một cho được trong với không này đó thì gì nào bao nhiêu như thế về để khi đã sẽ ở ra vào cũng hay hoặc nếu mà tôi bạn còn sao theo từ trên dưới tại đây kia nó họ "
    + "the a an is are was were of to in and or what how why which who whom does do did for on with it its this that these those be by as at from can about into than then there their they you your").split(" "));
  function keywordSet(...texts) {
    const set = new Set();
    for (const t of texts) for (const w of (t || "").toLowerCase().match(/[\p{L}\p{N}]+/gu) || []) {
      if (w.length >= 2 && !STOP.has(w)) set.add(w);
    }
    return set;
  }
  function highlight(text, words) {
    if (!words.size) return escapeHtml(text);
    let out = "", last = 0;
    for (const m of text.matchAll(/[\p{L}\p{N}]+/gu)) {
      if (words.has(m[0].toLowerCase())) {
        out += escapeHtml(text.slice(last, m.index)) + "<mark>" + escapeHtml(m[0]) + "</mark>";
        last = m.index + m[0].length;
      }
    }
    return out + escapeHtml(text.slice(last));
  }

  // ================================================================ sức khỏe Ollama
  async function checkHealth(manual = false) {
    let h;
    try { h = await apiJson("/api/health"); }
    catch { showBanner(SERVER_DOWN.message, ""); state.healthOk = false; return; }
    state.config = { ...state.config, ...(h.config || {}) };
    $$(".max-mb").forEach((e) => { e.textContent = state.config.max_upload_mb; });
    $("#model-badge").textContent = h.llm_model || "";
    if (h.ok) {
      if (state.healthOk === false || manual) toast("Đã kết nối Ollama, model sẵn sàng.");
      hideBanner();
    } else {
      const p = h.problems || [];
      const down = p.find((x) => x.code === "ollama_down");
      if (down) showBanner("Ollama chưa chạy.", "Mở ứng dụng Ollama hoặc chạy: ollama serve");
      else {
        const missing = p.filter((x) => x.code === "model_missing").map((x) => x.hint.replace(/^Chạy lệnh:\s*/, ""));
        showBanner("Chưa tải đủ model cho Ollama.", missing.join("  &&  "));
      }
    }
    state.healthOk = !!h.ok;
  }
  let healthTimer = null;
  function showBanner(text, hint) {
    $("#banner-text").textContent = text;
    $("#banner-hint").textContent = hint || "";
    $("#banner").hidden = false;
    clearInterval(healthTimer);
    healthTimer = setInterval(() => checkHealth(), 15000);
  }
  function hideBanner() { $("#banner").hidden = true; clearInterval(healthTimer); }

  // ================================================================ tài liệu (cột trái)
  function applyDoc(payload) {
    state.docs = payload.docs || [];
    state.stats = payload.stats || null;
    state.suggestions = payload.suggestions || { status: "none", items: [] };
    renderDocs();
    renderEmpty();
    map.load();
    if (state.suggestions.status === "pending") pollSuggestions();
  }
  async function loadDoc() {
    try { applyDoc(await apiJson("/api/doc")); }
    catch { /* banner đã báo nếu server chết */ }
  }

  function renderDocs() {
    const list = $("#doc-list");
    list.innerHTML = "";
    const { docs } = state;
    for (const d of docs) {
      const card = document.createElement("article");
      card.className = "doc-card";
      const kind = (d.kind === "txt" ? (d.name.toLowerCase().endsWith(".md") ? "MD" : "TXT") : d.kind.toUpperCase());
      const meta = [d.pages ? nf.format(d.pages) + " trang" : null, nf.format(d.n_chunks) + " đoạn", fmtBytes(d.size)].filter(Boolean).join(" · ");
      card.innerHTML = `
        <div class="doc-card-head">
          <span class="doc-icon" aria-hidden="true">${escapeHtml(kind)}</span>
          <div><p class="doc-name" title="${escapeHtml(d.name)}">${escapeHtml(d.name)}</p><p class="doc-meta">${escapeHtml(meta)}</p></div>
        </div>
        <div class="doc-actions">
          <button type="button" class="btn btn-ghost" data-act="replace" data-id="${escapeHtml(d.id)}">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12a8 8 0 0 1 13.7-5.6L20 8.5M20 4v4.5h-4.5M20 12a8 8 0 0 1-13.7 5.6L4 15.5M4 20v-4.5h4.5"/></svg>Thay file</button>
          <button type="button" class="btn btn-ghost" data-act="remove" data-id="${escapeHtml(d.id)}" aria-label="Gỡ tài liệu ${escapeHtml(d.name)}">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>Gỡ</button>
        </div>`;
      list.append(card);
    }
    const has = docs.length > 0;
    $("#dropzone").hidden = has;
    $("#add-file-btn").hidden = !has || docs.length >= state.config.max_files || state.uploading;
    const st = state.stats;
    $("#stats").hidden = !has;
    if (st && has) {
      $("#st-chunks").textContent = nf.format(st.n_chunks);
      $("#st-dim").textContent = nf.format(st.dim);
      $("#st-tokens").textContent = nf.format(st.tokens);
      $("#st-dup").textContent = st.duplicates ? `${nf.format(st.duplicates)} cặp đoạn gần trùng lặp` : "";
    }
  }

  const STEPS = ["reading", "chunking", "embedding"];
  function setStep(stage, data = {}) {
    const idx = STEPS.indexOf(stage);
    $$(".steps li").forEach((li, i) => {
      li.classList.toggle("done", i < idx || stage === "done");
      li.classList.toggle("active", i === idx);
    });
    let pct = 0;
    if (stage === "reading") pct = 4;
    else if (stage === "chunking") pct = 10;
    else if (stage === "embedding") pct = 10 + 90 * ((data.done || 0) / Math.max(1, data.total || 1));
    else if (stage === "done") pct = 100;
    $("#progress-bar").style.width = pct.toFixed(1) + "%";
    $("#progress").setAttribute("aria-valuenow", String(Math.round(pct)));
    $("#embed-count").textContent = stage === "embedding" || stage === "done" ? (data.total ? `${data.done}/${data.total}` : "") : "";
    if (data.message) {
      let t = data.message;
      if (stage === "embedding" && data.eta_s > 15) {
        const m = Math.round(data.eta_s / 60);
        t += m >= 1 ? ` · còn khoảng ${m} phút` : ` · còn khoảng ${data.eta_s} giây`;
      }
      $("#progress-text").textContent = t;
    }
  }

  function showDzError(err) {
    const box = $("#dz-error");
    box.innerHTML = `<strong>${escapeHtml(err.message || "Không nạp được file.")}</strong>` +
      (err.hint ? `<span class="hint">${escapeHtml(err.hint)}</span>` : "");
    box.hidden = false;
    if (isMobile() && !document.body.classList.contains("drawer-open")) openDrawer();
  }

  const OK_EXT = ["pdf", "docx", "txt", "md", "markdown"];
  async function uploadFile(file, replaceId = null) {
    $("#dz-error").hidden = true;
    const ext = (file.name.split(".").pop() || "").toLowerCase();
    if (ext === "doc") return showDzError({ message: "File .doc (Word đời cũ) chưa được hỗ trợ.", hint: "Mở bằng Word/LibreOffice rồi lưu lại dạng .docx hoặc .pdf." });
    if (!OK_EXT.includes(ext)) return showDzError({ message: `Định dạng “.${ext}” chưa được hỗ trợ.`, hint: "Hãy dùng PDF, Word (.docx), .txt hoặc .md." });
    if (file.size > state.config.max_upload_mb * 1024 * 1024) {
      return showDzError({ message: `File quá lớn (${fmtBytes(file.size)}). Giới hạn là ${state.config.max_upload_mb} MB.`, hint: "Hãy tách file thành các phần nhỏ hơn." });
    }
    if (file.size === 0) return showDzError({ message: "File rỗng.", hint: "" });
    if (!replaceId && state.docs.length >= state.config.max_files) {
      return showDzError({ message: `Mỗi phiên chỉ nạp tối đa ${state.config.max_files} file.`, hint: "Gỡ bớt một file rồi thử lại." });
    }
    state.uploading = true;
    $("#dropzone").classList.add("is-busy");
    $("#add-file-btn").hidden = true;
    $("#ingest").hidden = false;
    $("#ingest-file").textContent = file.name;
    $("#progress-text").textContent = "Đang tải file lên…";
    setStep("reading", { message: "Đang tải file lên…" });
    let ok = false;
    try {
      const qs = "filename=" + encodeURIComponent(file.name) + (replaceId ? "&replace=" + encodeURIComponent(replaceId) : "");
      const resp = await api("/api/upload?" + qs, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file });
      if (!resp.ok) {
        const j = await resp.json().catch(() => ({}));
        showDzError({ message: j.message || "Không nạp được file (HTTP " + resp.status + ").", hint: j.hint || "" });
      } else {
        for await (const { event, data } of readSSE(resp)) {
          if (event === "progress") setStep(data.stage, data);
          else if (event === "error") showDzError(data);
          else if (event === "done") {
            ok = true;
            setStep("done", { message: "Đã nạp xong.", done: data.doc.n_chunks, total: data.doc.n_chunks });
            applyDoc(data);
            toast(`Đã nạp “${data.doc.name}” (${nf.format(data.doc.n_chunks)} đoạn)`);
          }
        }
      }
    } catch {
      showDzError(SERVER_DOWN);
    } finally {
      state.uploading = false;
      $("#dropzone").classList.remove("is-busy");
      setTimeout(() => { if (!state.uploading) $("#ingest").hidden = true; }, ok ? 900 : 0);
      renderDocs();
      if (ok && isMobile()) setTimeout(closeDrawer, 600);
    }
  }
  async function uploadMany(files) {
    for (const f of files) {
      if (state.uploading) break;
      await uploadFile(f, state.replaceId);
      state.replaceId = null;
      if (!$("#dz-error").hidden) break;
    }
  }

  // ================================================================ câu hỏi gợi ý
  let sugTimer = null;
  function pollSuggestions(deadline = Date.now() + 5 * 60 * 1000) {
    clearTimeout(sugTimer);
    sugTimer = setTimeout(async () => {
      try {
        const d = await apiJson("/api/doc");
        state.suggestions = d.suggestions;
        renderEmpty();
        if (d.suggestions.status === "pending" && Date.now() < deadline) pollSuggestions(deadline);
      } catch { /* bỏ qua: gợi ý không quan trọng */ }
    }, 2000);
  }

  function renderEmpty() {
    const box = $("#empty");
    box.hidden = state.messages.length > 0;
    if (box.hidden) return;
    if (!state.docs.length) {
      box.innerHTML = `
        <p class="empty-kicker">Hỏi đáp tài liệu · chạy hoàn toàn trên máy bạn</p>
        <h1>Bắt đầu bằng một tài liệu</h1>
        <p>Nạp một file PDF, Word, .txt hoặc .md. docqa sẽ đọc, chia đoạn và tạo vector ngay trên máy này, rồi trả lời câu hỏi của bạn kèm trích dẫn nguồn.</p>
        <ol class="empty-steps">
          <li>Kéo thả file vào ${isMobile() ? "ngăn Tài liệu (nút ☰ phía trên)" : "cột bên trái"}.</li>
          <li>Chờ tạo vector xong (thanh tiến độ).</li>
          <li>Đặt câu hỏi bằng tiếng Việt hoặc tiếng Anh.</li>
        </ol>`;
      return;
    }
    const names = state.docs.map((d) => d.name);
    const sug = state.suggestions || {};
    let sugHtml = "";
    if (sug.status === "pending") {
      sugHtml = `<div class="suggest"><h3 class="section-label">Câu hỏi gợi ý</h3>
        <div class="suggest-list" aria-busy="true" aria-label="Đang chuẩn bị câu hỏi gợi ý"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>
        <p class="small muted">Đang chuẩn bị câu hỏi gợi ý… bạn vẫn có thể hỏi ngay.</p></div>`;
    } else if (sug.status === "done" && sug.items?.length) {
      sugHtml = `<div class="suggest"><h3 class="section-label">Câu hỏi gợi ý</h3><div class="suggest-list">` +
        sug.items.map((q) => `<button type="button" class="suggest-btn" data-q="${escapeHtml(q)}">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg><span>${escapeHtml(q)}</span></button>`).join("") +
        `</div></div>`;
    }
    box.innerHTML = `
      <p class="empty-kicker">${names.length > 1 ? `${names.length} tài liệu đã sẵn sàng` : "Tài liệu đã sẵn sàng"}</p>
      <h1>${names.map(escapeHtml).join("<br>")}</h1>
      <p>Hãy hỏi bất cứ điều gì về nội dung. Câu trả lời chỉ dựa trên tài liệu và có đánh số nguồn để bạn kiểm tra.</p>
      ${sugHtml}`;
  }

  // ================================================================ hội thoại
  const messagesEl = $("#messages");
  const scroller = $("#scroller");
  const nearBottom = () => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120;
  const scrollToBottom = () => { scroller.scrollTop = scroller.scrollHeight; };

  const STAGE_TEXT = {
    rewriting: "Đang hiểu câu hỏi trong ngữ cảnh…",
    searching: "Đang tìm trong tài liệu…",
    writing: "Đang viết câu trả lời…",
  };

  function messageNode(m) {
    const wrap = document.createElement("div");
    wrap.id = "m-" + m.id;
    if (m.role === "user") {
      wrap.className = "msg msg-user";
      const b = document.createElement("div");
      b.className = "bubble";
      b.textContent = m.content;
      wrap.append(b);
    } else {
      wrap.className = "msg msg-ai";
      wrap.innerHTML = `<div class="bubble"></div>`;
      fillAi(wrap, m);
    }
    return wrap;
  }

  function fillAi(wrap, m) {
    const bubble = wrap.firstElementChild;
    const nSrc = m.sources?.length || 0;
    let html = "";
    if (m.rewritten) html += `<p class="rewritten">Đã hiểu câu hỏi là: <em>“${escapeHtml(m.rewritten)}”</em></p>`;
    if (m.pending && !m.content) {
      const sec = Math.round((Date.now() - (m.started || Date.now())) / 1000);
      html += `<div class="thinking"><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>
        <span>${escapeHtml(STAGE_TEXT[m.stage] || "Đang xử lý…")}</span><span class="elapsed">${sec >= 3 ? sec + " giây" : ""}</span></div>`;
    }
    if (m.content) {
      if (m.lowRelevance) html += `<div class="note note-low">${escapeHtml(m.content)}</div>`;
      else html += `<div class="md">${renderMarkdown(m.content, nSrc)}</div>`;
    }
    if (m.stopped) html += `<p class="note">Đã dừng.</p>`;
    if (m.error) {
      html += `<div class="msg-error">${escapeHtml(m.error.message || "Đã có lỗi.")}${m.error.hint ? ` <code>${escapeHtml(m.error.hint)}</code>` : ""}</div>`;
    }
    if (!m.pending) {
      html += `<div class="msg-actions">
        ${m.content ? `<button type="button" class="btn btn-ghost" data-act="copy"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5"/></svg>Sao chép</button>` : ""}
        <button type="button" class="btn btn-ghost" data-act="regen"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12a8 8 0 0 1 13.7-5.6L20 8.5M20 4v4.5h-4.5M20 12a8 8 0 0 1-13.7 5.6L4 15.5M4 20v-4.5h4.5"/></svg>Tạo lại</button>
        ${nSrc ? `<button type="button" class="btn btn-ghost" data-act="sources"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 4h9l3 3v13H6z"/><path d="M9 11h6M9 15h6"/></svg>Nguồn (${nSrc})</button>` : ""}
      </div>`;
    }
    bubble.innerHTML = html;
    if (m.pending && m.content) {
      // con trỏ nhấp nháy ở cuối nội dung đang stream
      const md = $(".md", bubble);
      let target = md?.lastElementChild || md;
      while (target && ["UL", "OL"].includes(target.tagName) && target.lastElementChild) target = target.lastElementChild;
      if (target?.tagName === "PRE") target = target.firstElementChild || target;
      const c = document.createElement("span");
      c.className = "cursor"; c.setAttribute("aria-hidden", "true");
      (target || bubble).append(c);
    }
    wrap.setAttribute("aria-busy", m.pending ? "true" : "false");
  }

  function renderMessages() {
    messagesEl.innerHTML = "";
    for (const m of state.messages) messagesEl.append(messageNode(m));
    renderEmpty();
  }

  const pendingRender = new Set();
  function scheduleRender(m) {
    if (pendingRender.has(m)) return;
    pendingRender.add(m);
    requestAnimationFrame(() => {
      pendingRender.delete(m);
      const node = document.getElementById("m-" + m.id);
      if (!node) return;
      const stick = nearBottom();
      fillAi(node, m);
      if (stick) scrollToBottom();
    });
  }
  setInterval(() => {
    const g = state.generating;
    if (g && !g.msg.content) scheduleRender(g.msg);
  }, 1000);

  function setGenerating(on) {
    $("#composer").classList.toggle("is-generating", on);
    $("#send-btn").setAttribute("aria-label", on ? "Dừng sinh câu trả lời" : "Gửi câu hỏi");
  }

  async function ask(question, regenMsg = null) {
    if (state.generating) return;
    if (!state.docs.length) {
      toast("Hãy nạp tài liệu trước khi đặt câu hỏi.", "err");
      if (isMobile()) openDrawer();
      return;
    }
    let ai, histEnd;
    if (regenMsg) {
      ai = regenMsg;
      const i = state.messages.indexOf(ai);
      const u = state.messages[i - 1];
      if (!u || u.role !== "user") return;
      question = u.content;
      histEnd = i - 1;
      Object.assign(ai, { content: "", sources: null, queryXY: null, rewritten: null, error: null, stopped: false, lowRelevance: false });
    } else {
      const u = { id: uid(), role: "user", content: question };
      ai = { id: uid(), role: "assistant", content: "" };
      state.messages.push(u, ai);
      histEnd = state.messages.length - 2;
      messagesEl.append(messageNode(u), messageNode(ai));
      renderEmpty();
    }
    Object.assign(ai, { pending: true, stage: "searching", started: Date.now(), question });
    scheduleRender(ai);
    requestAnimationFrame(scrollToBottom);

    // Chỉ gửi các lượt có câu trả lời thật: lượt "không tìm thấy"/lỗi bị bỏ cùng câu hỏi của nó,
    // nếu không model nhỏ dễ trả lời lại câu hỏi cũ.
    const UNANSWERED = /^(không tìm thấy trong tài liệu|not found in the document)/i;
    const history = [];
    for (const m of state.messages.slice(0, histEnd)) {
      if (m.role === "user") { history.push({ role: "user", content: m.content }); continue; }
      const bad = !m.content || m.error || m.lowRelevance || m.pending || UNANSWERED.test(m.content.trim());
      if (bad) { if (history.length && history[history.length - 1].role === "user") history.pop(); continue; }
      history.push({ role: "assistant", content: m.content });
    }
    history.splice(0, Math.max(0, history.length - 8));
    const ctrl = new AbortController();
    state.generating = { ctrl, msg: ai };
    setGenerating(true);
    saveChat();
    try {
      const resp = await api("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, history }),
        signal: ctrl.signal,
      });
      if (!resp.ok) {
        const j = await resp.json().catch(() => ({}));
        ai.error = { message: j.message || (Array.isArray(j.detail) ? "Câu hỏi không hợp lệ (tối đa 2000 ký tự)." : "Lỗi HTTP " + resp.status), hint: "" };
      } else {
        for await (const { event, data } of readSSE(resp)) {
          if (event === "status") ai.stage = data.stage;
          else if (event === "rewritten") ai.rewritten = data.question;
          else if (event === "sources") {
            ai.sources = data.sources; ai.queryXY = data.query_xy;
            showSources(ai);
          } else if (event === "token") { ai.content += data.text; ai.stage = "writing"; }
          else if (event === "done") ai.lowRelevance = !!data.low_relevance;
          else if (event === "error") ai.error = data;
          scheduleRender(ai);
        }
      }
    } catch (e) {
      if (e.name === "AbortError") ai.stopped = true;
      else ai.error = SERVER_DOWN;
    } finally {
      ai.pending = false;
      state.generating = null;
      setGenerating(false);
      scheduleRender(ai);
      if (ai.sources && state.activeMsgId === ai.id) showSources(ai);
      saveChat();
    }
  }

  // ================================================================ panel nguồn
  function findMsg(id) { return state.messages.find((m) => m.id === id); }

  function showSources(m, flashN = null) {
    state.activeMsgId = m?.id || null;
    const list = $("#sources-list");
    const cap = $("#sources-caption");
    const srcs = m?.sources || [];
    $("#src-count").textContent = srcs.length ? String(srcs.length) : "";
    if (!srcs.length) {
      cap.textContent = "";
      list.innerHTML = `<p class="pane-empty">${state.docs.length ? "Đặt một câu hỏi để xem các đoạn được dùng làm nguồn." : "Chưa có tài liệu."}</p>`;
      map.setHighlight(null);
      return;
    }
    const q = m.rewritten || m.question || "";
    cap.innerHTML = `Cho câu hỏi: <q>${escapeHtml(q.length > 140 ? q.slice(0, 140) + "…" : q)}</q>` +
      (m.lowRelevance ? ` · <strong>độ tương đồng thấp, có thể không liên quan</strong>` : "");
    const words = keywordSet(m.question, m.rewritten);
    list.innerHTML = srcs.map((s) => {
      const loc = [s.doc_name, s.page ? "trang " + s.page + (s.page_end && s.page_end !== s.page ? "–" + s.page_end : "") : null, "đoạn " + ((s.idx ?? 0) + 1)].filter(Boolean).join(" · ");
      const pct = Math.max(0, Math.min(1, s.score)) * 100;
      return `<article class="src-card" id="src-${s.n}" data-n="${s.n}">
        <button type="button" class="src-toggle" aria-expanded="false" aria-label="Nguồn ${s.n}: ${escapeHtml(loc)}, độ tương đồng ${s.score.toFixed(2)}. Bấm để mở rộng.">
          <span class="src-top"><span class="src-n">${s.n}</span><span class="src-loc">${escapeHtml(loc)}</span><span class="src-score">${s.score.toFixed(2)}</span></span>
          <span class="score-bar" aria-hidden="true"><span data-w="${pct.toFixed(1)}"></span></span>
          <span class="src-text">${highlight(s.text, words)}</span>
          <span class="src-more" aria-hidden="true">Xem toàn đoạn</span>
        </button></article>`;
    }).join("");
    $$(".score-bar span", list).forEach((e) => { e.style.width = e.dataset.w + "%"; });
    map.setHighlight({ sources: srcs, q: m.queryXY });
    if (flashN) flashSource(flashN);
  }

  function flashSource(n) {
    const card = document.getElementById("src-" + n);
    if (!card) return;
    card.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "nearest" });
    card.classList.remove("flash"); void card.offsetWidth; card.classList.add("flash");
  }

  $("#sources-list").addEventListener("click", (e) => {
    const btn = e.target.closest(".src-toggle");
    if (!btn) return;
    const card = btn.parentElement;
    const open = card.classList.toggle("open");
    btn.setAttribute("aria-expanded", String(open));
    $(".src-more", card).textContent = open ? "Thu gọn" : "Xem toàn đoạn";
  });

  messagesEl.addEventListener("click", async (e) => {
    const cite = e.target.closest(".cite");
    const act = e.target.closest("[data-act]");
    const node = e.target.closest(".msg-ai");
    const m = node && findMsg(node.id.slice(2));
    if (!m) return;
    if (cite) {
      const n = parseInt(cite.dataset.n, 10);
      openSourcesView();
      showSources(m, n);
      return;
    }
    if (!act) return;
    if (act.dataset.act === "copy") {
      const text = m.content;
      try { await navigator.clipboard.writeText(text); }
      catch {
        const ta = Object.assign(document.createElement("textarea"), { value: text });
        document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove();
      }
      toast("Đã sao chép câu trả lời");
    } else if (act.dataset.act === "regen") {
      ask(null, m);
    } else if (act.dataset.act === "sources") {
      openSourcesView();
      showSources(m);
    }
  });

  function openSourcesView() {
    if (isMobile()) setView("sources");
    else {
      if (document.body.classList.contains("panel-collapsed")) togglePanel(false);
      selectTab("sources");
    }
  }

  // ================================================================ bản đồ vector
  const map = (() => {
    const canvas = $("#map");
    const wrap = $("#map-wrap");
    const tip = $("#map-tip");
    const ctx = canvas.getContext("2d");
    let W = 0, H = 0, dpr = 1;
    let points = [];        // {id,x,y,pos,idx,doc_name,page,preview}
    let info = { explained: [0, 0], dim: 0, duplicates: { count: 0, pairs: [] } };
    let bounds = null;      // {cx, cy, s}
    const view = { k: 1, x: 0, y: 0 };
    let hl = null;          // {hits: Map(id → {n, score}), q:[x,y], min, max}
    let hover = -1, sel = -1;
    let loadedFor = null;

    function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
    function hexToRgb(h) {
      h = h.replace("#", "");
      if (h.length === 3) h = h.split("").map((c) => c + c).join("");
      const n = parseInt(h, 16);
      return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    }
    function ramp(t, stops) {
      t = Math.max(0, Math.min(1, t));
      const seg = t < 0.5 ? 0 : 1;
      const lt = t < 0.5 ? t * 2 : (t - 0.5) * 2;
      const a = stops[seg], b = stops[seg + 1];
      return a.map((v, i) => Math.round(v + (b[i] - v) * lt));
    }
    const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

    function fit() {
      const pts = points.map((p) => [p.x, p.y]);
      if (hl?.q) pts.push(hl.q);
      if (!pts.length || !W) { bounds = null; return; }
      let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
      for (const [x, y] of pts) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
      const pad = 30;
      const sx = (W - pad * 2) / Math.max(x1 - x0, 1e-6);
      const sy = (H - pad * 2 - 30) / Math.max(y1 - y0, 1e-6);
      bounds = { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, s: Math.min(sx, sy, 1e6) };
      if (x1 - x0 < 1e-6 && y1 - y0 < 1e-6) bounds.s = 1;
    }
    function toScreen(x, y) {
      return [W / 2 + view.x + (x - bounds.cx) * bounds.s * view.k, H / 2 + 14 + view.y - (y - bounds.cy) * bounds.s * view.k];
    }

    function resize() {
      const r = wrap.getBoundingClientRect();
      if (r.width < 10 || r.height < 10) return;
      dpr = window.devicePixelRatio || 1;
      W = r.width; H = r.height;
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      fit(); draw();
    }

    function star(cx, cy, r) {
      ctx.beginPath();
      for (let i = 0; i < 10; i++) {
        const rr = i % 2 ? r * 0.45 : r;
        const a = -Math.PI / 2 + (i * Math.PI) / 5;
        ctx.lineTo(cx + rr * Math.cos(a), cy + rr * Math.sin(a));
      }
      ctx.closePath();
    }

    function draw() {
      if (!W) return;
      ctx.clearRect(0, 0, W, H);
      $("#map-empty").hidden = points.length > 0;
      if (!points.length || !bounds) return;
      const stops = [hexToRgb(css("--ramp-0")), hexToRgb(css("--ramp-1")), hexToRgb(css("--ramp-2"))];
      const accent = hexToRgb(css("--accent"));
      const surface = css("--surface");
      const text = css("--text");
      const r = Math.max(2.5, Math.min(6, 3.4 * Math.sqrt(view.k)));
      const scr = points.map((p) => toScreen(p.x, p.y));

      // đường nối câu hỏi → đoạn được truy xuất
      if (hl?.q) {
        const [qx, qy] = toScreen(hl.q[0], hl.q[1]);
        points.forEach((p, i) => {
          const h = hl.hits.get(p.id);
          if (!h) return;
          const t = hl.max > hl.min ? (h.score - hl.min) / (hl.max - hl.min) : 1;
          ctx.strokeStyle = rgba(accent, 0.25 + 0.65 * t);
          ctx.lineWidth = 1 + 1.6 * t;
          ctx.beginPath(); ctx.moveTo(qx, qy); ctx.lineTo(scr[i][0], scr[i][1]); ctx.stroke();
        });
      }
      // các chấm thường
      points.forEach((p, i) => {
        if (hl?.hits.has(p.id)) return;
        ctx.fillStyle = rgba(ramp(p.pos, stops), hl ? 0.32 : 0.88);
        ctx.beginPath(); ctx.arc(scr[i][0], scr[i][1], r, 0, Math.PI * 2); ctx.fill();
      });
      // các đoạn được truy xuất: nổi lên trên, viền màu nhấn, có số thứ tự
      if (hl) {
        ctx.font = "600 11px system-ui, sans-serif";
        points.forEach((p, i) => {
          const h = hl.hits.get(p.id);
          if (!h) return;
          const [x, y] = scr[i];
          ctx.fillStyle = rgba(ramp(p.pos, stops), 1);
          ctx.beginPath(); ctx.arc(x, y, r + 2, 0, Math.PI * 2); ctx.fill();
          ctx.lineWidth = 2.2; ctx.strokeStyle = rgba(accent, 1);
          ctx.beginPath(); ctx.arc(x, y, r + 4, 0, Math.PI * 2); ctx.stroke();
          ctx.fillStyle = text;
          ctx.fillText(String(h.n), x + r + 6, y - r - 2);
        });
      }
      // chấm đang rê chuột / đang chọn
      for (const i of [hover, sel]) {
        if (i < 0 || !scr[i]) continue;
        ctx.lineWidth = 1.5; ctx.strokeStyle = text;
        ctx.beginPath(); ctx.arc(scr[i][0], scr[i][1], r + 6, 0, Math.PI * 2); ctx.stroke();
      }
      // ngôi sao câu hỏi
      if (hl?.q) {
        const [qx, qy] = toScreen(hl.q[0], hl.q[1]);
        star(qx, qy, 10);
        ctx.fillStyle = rgba(accent, 1); ctx.fill();
        ctx.lineWidth = 2; ctx.strokeStyle = surface; ctx.stroke();
      }
    }

    function nearest(mx, my, maxD = 12) {
      if (!bounds) return -1;
      let best = -1, bd = maxD * maxD;
      points.forEach((p, i) => {
        const [x, y] = toScreen(p.x, p.y);
        const d = (x - mx) ** 2 + (y - my) ** 2;
        if (d < bd) { bd = d; best = i; }
      });
      return best;
    }
    function label(p) {
      return `Đoạn ${p.idx + 1} · ${p.doc_name}${p.page ? " · trang " + p.page + (p.page_end && p.page_end !== p.page ? "–" + p.page_end : "") : ""}`;
    }
    function showTip(i) {
      if (i < 0) { tip.hidden = true; return; }
      const p = points[i];
      const h = hl?.hits.get(p.id);
      tip.innerHTML = `<b>${escapeHtml(label(p))}${h ? ` · nguồn [${h.n}] · ${h.score.toFixed(2)}` : ""}</b>${escapeHtml(p.preview)}${p.preview.length >= 220 ? "…" : ""}`;
      tip.hidden = false;
      const [x, y] = toScreen(p.x, p.y);
      const tw = tip.offsetWidth, th = tip.offsetHeight;
      let left = x + 14, top = y + 14;
      if (left + tw > W - 6) left = x - tw - 14;
      if (top + th > H - 6) top = y - th - 14;
      tip.style.left = Math.max(6, left) + "px";
      tip.style.top = Math.max(6, top) + "px";
    }
    async function select(i, focusList = false) {
      sel = i;
      draw();
      $$("#map-list button").forEach((b) => b.classList.toggle("sel", +b.dataset.i === i));
      const box = $("#map-detail");
      if (i < 0) { box.hidden = true; return; }
      const p = points[i];
      box.hidden = false;
      box.innerHTML = `<h4>${escapeHtml(label(p))}</h4><p>${escapeHtml(p.preview)}…</p>`;
      try {
        const c = await apiJson("/api/chunk/" + encodeURIComponent(p.id));
        if (sel === i) box.innerHTML = `<h4>${escapeHtml(label(p))}</h4><p>${escapeHtml(c.text)}</p>`;
      } catch { /* giữ bản xem trước */ }
      if (focusList) $(`#map-list button[data-i="${i}"]`)?.focus();
    }

    // --- tương tác chuột / cảm ứng
    let drag = null;
    canvas.addEventListener("pointerdown", (e) => {
      canvas.setPointerCapture(e.pointerId);
      drag = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, moved: false };
    });
    canvas.addEventListener("pointermove", (e) => {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      if (drag) {
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 3) { drag.moved = true; canvas.classList.add("dragging"); tip.hidden = true; }
        if (drag.moved) { view.x = drag.vx + dx; view.y = drag.vy + dy; draw(); }
        return;
      }
      const i = nearest(mx, my);
      if (i !== hover) { hover = i; draw(); }
      showTip(i);
    });
    const endDrag = (e) => {
      if (!drag) return;
      const wasClick = !drag.moved;
      drag = null;
      canvas.classList.remove("dragging");
      if (wasClick && e.type === "pointerup") {
        const rect = canvas.getBoundingClientRect();
        select(nearest(e.clientX - rect.left, e.clientY - rect.top, 16));
      }
    };
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);
    canvas.addEventListener("pointerleave", () => { if (!drag) { hover = -1; tip.hidden = true; draw(); } });
    function zoomAt(f, mx = W / 2, my = H / 2) {
      const k2 = Math.max(0.4, Math.min(60, view.k * f));
      const real = k2 / view.k;
      view.x = mx - W / 2 - (mx - W / 2 - view.x) * real;
      view.y = my - H / 2 - 14 - (my - H / 2 - 14 - view.y) * real;
      view.k = k2;
      draw();
    }
    canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - rect.left, e.clientY - rect.top);
      tip.hidden = true;
    }, { passive: false });
    function reset() { view.k = 1; view.x = 0; view.y = 0; fit(); draw(); }
    $("#map-reset").addEventListener("click", reset);
    $("#map-zoom-in").addEventListener("click", () => zoomAt(1.4));
    $("#map-zoom-out").addEventListener("click", () => zoomAt(1 / 1.4));

    // --- bàn phím
    canvas.addEventListener("keydown", (e) => {
      if (!points.length) return;
      let i = sel < 0 ? (hover < 0 ? -1 : hover) : sel;
      const k = e.key;
      if (k === "ArrowRight" || k === "ArrowDown") i = Math.min(points.length - 1, i + 1);
      else if (k === "ArrowLeft" || k === "ArrowUp") i = Math.max(0, i < 0 ? 0 : i - 1);
      else if (k === "Home") i = 0;
      else if (k === "End") i = points.length - 1;
      else if (k === "+" || k === "=") { zoomAt(1.4); e.preventDefault(); return; }
      else if (k === "-" || k === "_") { zoomAt(1 / 1.4); e.preventDefault(); return; }
      else if (k === "0") { reset(); e.preventDefault(); return; }
      else if (k === "Escape") { select(-1); tip.hidden = true; return; }
      else return;
      e.preventDefault();
      hover = -1;
      select(i);
      showTip(i);
    });
    canvas.addEventListener("blur", () => { tip.hidden = true; });

    // --- danh sách thay thế (dựng khi mở)
    const details = $(".map-list-wrap");
    function renderList() {
      const ol = $("#map-list");
      if (!details.open) { ol.innerHTML = ""; return; }
      ol.innerHTML = points.map((p, i) => {
        const h = hl?.hits.get(p.id);
        return `<li><button type="button" data-i="${i}" class="${i === sel ? "sel" : ""}">
          <span class="ml-head">${escapeHtml(label(p))} · (${p.x.toFixed(2)}, ${p.y.toFixed(2)})${h ? ` · <span class="ml-hit">nguồn [${h.n}] · ${h.score.toFixed(2)}</span>` : ""}</span>
          ${escapeHtml(p.preview.slice(0, 140))}${p.preview.length > 140 ? "…" : ""}</button></li>`;
      }).join("");
    }
    details.addEventListener("toggle", renderList);
    $("#map-list").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-i]");
      if (b) select(+b.dataset.i);
    });

    function renderStats() {
      const n = points.length;
      if (!n) { $("#map-stats").textContent = ""; return; }
      const pct = Math.round(((info.explained[0] || 0) + (info.explained[1] || 0)) * 100);
      const d = info.duplicates || { count: 0 };
      let s = `${nf.format(n)} đoạn · ${nf.format(info.dim)} chiều → 2D bằng PCA (giữ ${pct}% phương sai).`;
      s += d.count ? ` ${nf.format(d.count)} cặp đoạn gần trùng lặp (cosine ≥ ${d.threshold}).` : " Không có đoạn gần trùng lặp.";
      $("#map-stats").textContent = s;
    }

    async function load() {
      if (!state.docs.length) {
        points = []; hl = null; sel = -1; loadedFor = null;
        $("#map-detail").hidden = true;
        renderStats(); renderList(); draw();
        return;
      }
      const key = state.docs.map((d) => d.id).join(",");
      if (key === loadedFor) return;
      try {
        const d = await apiJson("/api/map");
        points = d.points; info = d; loadedFor = key; sel = -1;
        $("#map-detail").hidden = true;
        view.k = 1; view.x = 0; view.y = 0;
        // đoạn nổi bật hiện tại có thể đã không còn (đổi tài liệu)
        if (hl) setHighlight(hl.raw, true);
        fit(); renderStats(); renderList(); draw();
      } catch { /* bỏ qua */ }
    }

    function setHighlight(h, silent = false) {
      if (!h || !h.sources?.length) { hl = null; }
      else {
        const hits = new Map(h.sources.flatMap((s) => (s.ids || [s.id]).map((id) => [id, { n: s.n, score: s.score }])));
        const scores = h.sources.map((s) => s.score);
        hl = { hits, q: h.q, min: Math.min(...scores), max: Math.max(...scores), raw: h };
      }
      if (silent) return;
      fit(); renderList(); draw();
    }

    new ResizeObserver(resize).observe(wrap);
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener?.("change", draw);
    return { load, setHighlight, draw, resize, reset };
  })();

  // ================================================================ tab, view, ngăn kéo, panel
  function selectTab(name) {
    const tabs = { sources: $("#tab-sources"), map: $("#tab-map") };
    for (const [k, t] of Object.entries(tabs)) {
      const on = k === name;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      $("#" + t.getAttribute("aria-controls")).hidden = !on;
    }
    store.set("docqa:tab", name);
    if (name === "map") requestAnimationFrame(map.resize);
  }
  $(".tabs").addEventListener("click", (e) => {
    const t = e.target.closest("[role=tab]");
    if (t) selectTab(t.id === "tab-map" ? "map" : "sources");
  });
  $(".tabs").addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    const cur = $("#tab-map").getAttribute("aria-selected") === "true" ? "map" : "sources";
    const next = cur === "map" ? "sources" : "map";
    selectTab(e.key === "Home" ? "sources" : e.key === "End" ? "map" : next);
    $(next === "map" || e.key === "End" ? "#tab-map" : "#tab-sources").focus();
    e.preventDefault();
  });

  function setView(v) {
    document.body.dataset.view = v;
    $$(".bottom-tabs button").forEach((b) => {
      if (b.dataset.view === v) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
    if (v !== "chat") selectTab(v);
  }
  $(".bottom-tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-view]");
    if (b) setView(b.dataset.view);
  });

  function openDrawer() {
    document.body.classList.add("drawer-open");
    $("#backdrop").hidden = false;
    $("#drawer-btn").setAttribute("aria-expanded", "true");
    setTimeout(() => $("#drawer-close").focus(), 50);
  }
  function closeDrawer() {
    if (!document.body.classList.contains("drawer-open")) return;
    document.body.classList.remove("drawer-open");
    $("#backdrop").hidden = true;
    $("#drawer-btn").setAttribute("aria-expanded", "false");
    $("#drawer-btn").focus();
  }
  $("#drawer-btn").addEventListener("click", openDrawer);
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#backdrop").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && document.body.classList.contains("drawer-open")) closeDrawer(); });

  function togglePanel(collapse) {
    const c = collapse ?? !document.body.classList.contains("panel-collapsed");
    document.body.classList.toggle("panel-collapsed", c);
    const b = $("#panel-toggle");
    b.setAttribute("aria-expanded", String(!c));
    b.setAttribute("aria-label", c ? "Hiện bảng nguồn" : "Ẩn bảng nguồn");
    store.set("docqa:panelCollapsed", c);
    if (!c) setTimeout(map.resize, 220);
  }
  $("#panel-toggle").addEventListener("click", () => togglePanel());

  // ================================================================ giao diện sáng / tối
  function effectiveTheme() {
    const t = document.documentElement.getAttribute("data-theme");
    if (t) return t;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function updateThemeLabel() {
    $("#theme-label").textContent = effectiveTheme() === "dark" ? "Giao diện sáng" : "Giao diện tối";
  }
  $("#theme-btn").addEventListener("click", () => {
    const next = effectiveTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    // theme-init.js đọc chuỗi thô
    try { localStorage.setItem("docqa:theme", next); } catch { /* bỏ qua */ }
    updateThemeLabel();
    map.draw();
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", updateThemeLabel);

  // ================================================================ ô nhập
  const input = $("#composer-input");
  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 220) + "px";
  }
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => {
    // e.isComposing: đang gõ bộ gõ tiếng Việt (Telex/VNI) → Enter là để chốt chữ, không gửi
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
      e.preventDefault();
      $("#composer").requestSubmit();
    }
  });
  $("#composer").addEventListener("submit", (e) => {
    e.preventDefault();
    if (state.generating) { state.generating.ctrl.abort(); return; }
    const q = input.value.trim();
    if (!q) { input.focus(); return; }
    if (q.length > 2000) { toast("Câu hỏi quá dài (tối đa 2000 ký tự).", "err"); return; }
    input.value = ""; autosize();
    ask(q);
  });
  $("#empty").addEventListener("click", (e) => {
    const b = e.target.closest(".suggest-btn");
    if (b) ask(b.dataset.q);
  });

  // ================================================================ nạp file: chọn / kéo thả
  const fileInput = $("#file-input");
  $("#dz-button").addEventListener("click", () => { state.replaceId = null; fileInput.click(); });
  $("#add-file-btn").addEventListener("click", () => { state.replaceId = null; fileInput.click(); });
  fileInput.addEventListener("change", () => {
    const files = Array.from(fileInput.files || []);
    fileInput.value = "";
    if (files.length) uploadMany(state.replaceId ? files.slice(0, 1) : files);
  });
  $("#doc-list").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-act]");
    if (!b || state.uploading) return;
    const id = b.dataset.id;
    const doc = state.docs.find((d) => d.id === id);
    if (b.dataset.act === "replace") {
      state.replaceId = id;
      fileInput.click();
    } else if (b.dataset.act === "remove" && doc) {
      const last = state.docs.length === 1;
      const ok = await confirmDialog("Gỡ tài liệu?",
        `“${doc.name}” và các vector của nó sẽ bị xóa khỏi máy.` + (last ? " Đây là tài liệu cuối cùng nên lịch sử chat cũng sẽ bị xóa." : ""), "Gỡ");
      if (!ok) return;
      try {
        const d = await apiJson("/api/doc/" + encodeURIComponent(id), { method: "DELETE" });
        if (last) clearChatLocal();
        applyDoc(d);
        toast("Đã gỡ tài liệu");
      } catch { toast("Không gỡ được tài liệu.", "err"); }
    }
  });

  const dz = $("#dropzone");
  const overlay = $("#drop-overlay");
  let dragDepth = 0;
  const hasFiles = (e) => Array.from(e.dataTransfer?.types || []).includes("Files");
  window.addEventListener("dragenter", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth++;
    overlay.hidden = false;
    dz.classList.add("is-over");
  });
  window.addEventListener("dragover", (e) => { if (hasFiles(e)) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; } });
  window.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) { overlay.hidden = true; dz.classList.remove("is-over"); }
  });
  window.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth = 0; overlay.hidden = true; dz.classList.remove("is-over");
    const files = Array.from(e.dataTransfer.files || []);
    if (!files.length) return;
    if (state.uploading) { toast("Đang nạp một file khác, vui lòng chờ.", "err"); return; }
    state.replaceId = null;
    uploadMany(files);
  });

  // ================================================================ xóa sạch
  function clearChatLocal() {
    if (state.generating) state.generating.ctrl.abort();
    state.messages = [];
    store.del(chatKey());
    renderMessages();
    showSources(null);
  }
  $("#clear-btn").addEventListener("click", async () => {
    const ok = await confirmDialog("Xóa tài liệu & lịch sử?",
      "Toàn bộ tài liệu, vector và lịch sử chat của phiên này sẽ bị xóa vĩnh viễn khỏi máy. Không thể hoàn tác.", "Xóa tất cả");
    if (!ok) return;
    try { await apiJson("/api/session", { method: "DELETE" }); }
    catch { toast("Không xóa được dữ liệu trên server.", "err"); return; }
    clearChatLocal();
    // đổi sang session id mới cho sạch sẽ
    SID = newSid(); store.set("docqa:sid", SID);
    $("#dz-error").hidden = true;
    applyDoc({ docs: [], stats: null, suggestions: { status: "none", items: [] } });
    toast("Đã xóa tài liệu và lịch sử");
  });

  $("#banner-retry").addEventListener("click", () => checkHealth(true));

  // ================================================================ khởi động
  async function init() {
    updateThemeLabel();
    if (store.get("docqa:panelCollapsed")) togglePanel(true);
    selectTab(store.get("docqa:tab") === "map" ? "map" : "sources");
    renderMessages();
    await checkHealth();
    try {
      const d = await apiJson("/api/doc");
      // Phiên trên server đã bị dọn (quá hạn) nhưng trình duyệt còn chat cũ → xóa chat cho khớp
      if (!d.docs.length && state.messages.length) {
        state.messages = []; store.del(chatKey()); renderMessages();
        toast("Phiên cũ đã hết hạn nên lịch sử chat đã được xóa.");
      }
      applyDoc(d);
    } catch { /* server lỗi: banner đã báo */ }
    const lastWithSources = [...state.messages].reverse().find((m) => m.sources?.length);
    showSources(lastWithSources || null);
    requestAnimationFrame(scrollToBottom);
    if (!isMobile()) input.focus();
  }
  init();
})();
