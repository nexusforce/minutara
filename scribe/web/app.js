/* Scribe GUI. Plain JS, no build step, no network beyond this machine.
   Views: #/ (home), #/folder/<name>, #/note/<id>, #/settings. */
"use strict";

// --- plumbing ---------------------------------------------------------------
const token = new URLSearchParams(location.search).get("token") || "";
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const html = document.documentElement;
const esc = s => String(s ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const withToken = path => path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token);
const api = (path, opts = {}) =>
  fetch(withToken(path), { headers: { "Content-Type": "application/json" }, ...opts });
const getJSON = async path => (await api(path)).json();
async function send(method, path, body) {
  const r = await api(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
  let data = {};
  try { data = await r.json(); } catch {}
  return { ...data, ok: r.ok, status: r.status };
}
const noteUrl = (id, tail = "") => `/api/notes/${encodeURIComponent(id)}${tail}`;
const store = {
  get: k => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch {} },
};

// --- icons (hand-drawn 24px strokes, no icon font: this runs offline) --------
const ICONS = {
  home: '<path d="M3.5 10.5 12 3.5l8.5 7"/><path d="M5.5 9v11h13V9"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
  folder: '<path d="M3.5 7.5a2 2 0 0 1 2-2h3.8l2 2h7.2a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
  folderPlus: '<path d="M3.5 7.5a2 2 0 0 1 2-2h3.8l2 2h7.2a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/><path d="M12 11v5M9.5 13.5h5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  sliders: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  phone: '<rect x="7" y="2.5" width="10" height="19" rx="2.5"/><path d="M11 18.5h2"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
  moon: '<path d="M19.5 14.5A7.5 7.5 0 0 1 9.5 4.5a7.5 7.5 0 1 0 10 10z"/>',
  sidebar: '<rect x="3.5" y="4.5" width="17" height="15" rx="3"/><path d="M9.5 4.5v15"/>',
  download: '<path d="M12 4v11M7.5 10.5 12 15l4.5-4.5"/><path d="M5 19.5h14"/>',
  doc: '<path d="M14 3.5H8a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7.5z"/><path d="M14 3.5v4h4M9 12h6M9 15.5h4"/>',
  pocket: '<rect x="6.5" y="3" width="11" height="18" rx="4"/><circle cx="12" cy="8.5" r="1.4"/>',
  back: '<path d="m14.5 18-6-6 6-6"/>',
  more: '<circle cx="5.5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="18.5" cy="12" r="1.5"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="2"/>',
  pause: '<path d="M9 6v12M15 6v12"/>',
  play: '<path d="M8 5.5v13l10.5-6.5z"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>',
  trash: '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13"/>',
  x: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  calendar: '<rect x="3.5" y="5" width="17" height="15" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  list: '<path d="M9 6.5h11M9 12h11M9 17.5h11M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01"/>',
  power: '<path d="M12 3.5v8"/><path d="M6.6 7a7.5 7.5 0 1 0 10.8 0"/>',
  refresh: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4.5v4h-4"/>',
};
const FILLED = new Set(["more", "stop", "play"]);
function icon(name) {
  const attrs = FILLED.has(name)
    ? 'fill="currentColor" stroke="none"'
    : 'fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"';
  return `<svg class="i" viewBox="0 0 24 24" aria-hidden="true" ${attrs}>${ICONS[name] || ""}</svg>`;
}
function paintIcons(root = document) {
  $$("i[data-i]", root).forEach(el => { el.outerHTML = icon(el.dataset.i); });
}

// --- formatting ---------------------------------------------------------------
function clock(s) {
  s = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const mm = String(m).padStart(2, "0"), ss = String(sec).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}
const fmtTime = d => d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
function dayLabel(d) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const day = new Date(d); day.setHours(0, 0, 0, 0);
  const diff = Math.round((today - day) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  const opts = { weekday: "short", month: "short", day: "numeric" };
  if (day.getFullYear() !== today.getFullYear()) opts.year = "numeric";
  return day.toLocaleDateString([], opts);
}
function fmtDur(s) {
  if (!s) return "";
  const m = Math.round(s / 60);
  if (m < 1) return "under a minute";
  if (m < 60) return `${m} min`;
  return m % 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m / 60} h`;
}
const STATUS = {
  recording: ["Recording", "rec"], paused: ["Paused", "rec"], saving: ["Saving…", "rec"],
  transcribing: ["Transcribing…", "busy"], queued: ["Queued", "busy"],
  audio: ["Not transcribed", ""], error: ["Failed", "err"],
};
const LIVE = ["recording", "paused", "saving"];

// --- state --------------------------------------------------------------------
const S = {
  status: null, route: { view: "home" }, folders: [], config: null,
  noteId: null, note: null, lastSig: "", scrubbing: false, refreshT: null,
};
const player = $("#player");

let toastT;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toastT); toastT = setTimeout(() => { t.hidden = true; }, 3400);
}

// --- theme --------------------------------------------------------------------
const themePref = () => store.get("scribe-theme") || "system";
function applyTheme(pref) {
  store.set("scribe-theme", pref);
  const dark = pref === "dark" ||
    (pref === "system" && !matchMedia("(prefers-color-scheme: light)").matches);
  html.dataset.theme = dark ? "dark" : "light";
  $("#metatheme").content = dark ? "#262624" : "#ffffff";
  $("#theme-btn").innerHTML = icon(dark ? "moon" : "sun");
  $("#s-theme").value = pref;
}
matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
  if (themePref() === "system") applyTheme("system");
});
$("#theme-btn").onclick = () => applyTheme(html.dataset.theme === "dark" ? "light" : "dark");
$("#s-theme").onchange = e => applyTheme(e.target.value);

// --- sidebar ------------------------------------------------------------------
const narrow = () => matchMedia("(max-width: 820px)").matches;
function setCollapsed(v) { html.classList.toggle("side-collapsed", v); store.set("scribe-side", v ? "1" : "0"); }
function openSide() { if (narrow()) html.classList.add("side-open"); else setCollapsed(false); }
function closeSide() { html.classList.remove("side-open"); }
$("#side-toggle").onclick = () => (narrow() ? closeSide() : setCollapsed(true));
$$("[data-open-side]").forEach(b => { b.onclick = openSide; });
$("#scrim").onclick = closeSide;

function markNav() {
  const r = S.route;
  $$(".nav[data-nav]").forEach(a =>
    a.classList.toggle("on", a.dataset.nav === r.view && !r.folder && !$("#q").value.trim()));
  $$("#folders .nav").forEach(a => a.classList.toggle("on", r.folder === a.dataset.folder));
}

async function loadFolders() {
  try { S.folders = (await getJSON("/api/folders")).folders; } catch { return; }
  $("#folders").innerHTML = S.folders.map(f =>
    `<a class="nav" href="#/folder/${encodeURIComponent(f.name)}" data-folder="${esc(f.name)}">` +
    `${icon("folder")}<span>${esc(f.name)}</span><span class="count">${f.count || ""}</span>` +
    `<button class="nav-x" data-del-folder="${esc(f.name)}" title="Remove folder" aria-label="Remove folder">${icon("x")}</button></a>`
  ).join("");
  markNav();
  if (S.note) renderFolderSelect(S.note.folder);
}

$("#add-folder").onclick = async () => {
  const name = (prompt("New folder name") || "").trim();
  if (!name) return;
  const r = await send("POST", "/api/folders", { name });
  if (!r.ok) { toast(r.message || "Couldn't add that folder."); return; }
  await loadFolders();
  location.hash = `#/folder/${encodeURIComponent(r.name)}`;
};

$("#folders").addEventListener("click", async e => {
  const x = e.target.closest("[data-del-folder]");
  if (!x) return;
  e.preventDefault(); e.stopPropagation();
  const name = x.dataset.delFolder;
  if (!confirm(`Remove the folder “${name}”? Its meetings stay; they just leave the folder.`)) return;
  await send("DELETE", `/api/folders/${encodeURIComponent(name)}`);
  await loadFolders();
  if (S.route.folder === name) location.hash = "#/"; else refreshSoon();
});

// --- routing ------------------------------------------------------------------
function parseRoute() {
  const h = decodeURIComponent(location.hash.replace(/^#\/?/, ""));
  if (h.startsWith("note/")) return { view: "note", id: h.slice(5) };
  if (h === "settings") return { view: "settings" };
  if (h.startsWith("folder/")) return { view: "home", folder: h.slice(7) };
  return { view: "home" };
}

async function route() {
  const prev = S.route;
  S.route = parseRoute();
  clearTimeout(qT);  // a half-typed search must not yank you off this page
  closeSide(); closeMenu();
  if (prev.view === "note" && (S.route.view !== "note" || S.route.id !== prev.id)) leaveNote();
  for (const v of ["home", "note", "settings"]) $("#view-" + v).hidden = S.route.view !== v;
  if (S.route.view !== "home" || S.route.folder) $("#q").value = "";
  markNav();
  $("#main").scrollTop = 0;
  if (S.route.view === "home") await loadHome();
  else if (S.route.view === "note") await openNote(S.route.id);
  else await loadSettings();
}
window.addEventListener("hashchange", route);

function refreshSoon() { clearTimeout(S.refreshT); S.refreshT = setTimeout(refresh, 200); }
function refresh() {
  if (S.route.view === "home") loadHome();
  else if (S.route.view === "note" && S.noteId) openNote(S.noteId);
  loadFolders();
}

// --- status polling -----------------------------------------------------------
async function poll() {
  let s;
  try { s = await getJSON("/api/status"); } catch { $("#offline").hidden = false; return; }
  $("#offline").hidden = true;
  S.status = s;
  const t = s.transcriber || {};
  document.body.dataset.rec = s.state;
  document.body.classList.toggle("working", !!t.busy && !LIVE.includes(s.state));
  const cloud = $("#cloud-state");
  cloud.textContent = s.push_to_pocket ? "Pocket cloud on" : "Local only";
  cloud.classList.toggle("cloud", !!s.push_to_pocket);
  renderNow();
  if (S.route.view === "note") renderDock();

  // Something changed (a recording saved, a transcript finished): refresh.
  const sig = [s.state, s.note_id, t.name, t.queued, t.message, s.message].join("|");
  if (sig !== S.lastSig) {
    const first = !S.lastSig;
    S.lastSig = sig;
    if (!first) refreshSoon();
  }
}

// --- home ---------------------------------------------------------------------
function renderDate() {
  const d = new Date();
  $("#now-day").textContent = d.getDate();
  $("#now-month").textContent = d.toLocaleDateString([], { month: "long" });
  $("#now-wd").textContent = d.toLocaleDateString([], { weekday: "short" });
}

function renderNow() {
  const s = S.status;
  if (!s) return;
  renderDate();
  const live = LIVE.includes(s.state);
  $("#now-idle").hidden = live;
  $("#now-live").hidden = !live;
  if (live) {
    $("#live-state").textContent =
      s.state === "paused" ? "Paused" : s.state === "saving" ? "Saving…" : "Recording";
    $("#live-timer").textContent = clock(s.elapsed);
    $("#live-title").textContent = s.title || "Untitled meeting";
    const pb = $("#live-pause");
    pb.disabled = s.state === "saving";
    pb.innerHTML = icon(s.state === "paused" ? "play" : "pause") +
      (s.state === "paused" ? "Resume" : "Pause");
    $("#live-stop").disabled = s.state === "saving";
  } else {
    const hint = $("#now-hint");
    const err = s.state === "error";
    hint.classList.toggle("err", err);
    hint.textContent = err
      ? `The last recording failed: ${s.message}`
      : "Records your microphone and your computer's audio, entirely on this machine.";
  }
  const t = s.transcriber || {};
  $("#work").hidden = !t.busy;
  if (t.busy) {
    let text = `Transcribing “${t.title || t.name}”`;
    if (t.queued) text += ` · ${t.queued} more queued`;
    if (!t.model_ready) text += " · the first run downloads the speech model, so this one takes a while";
    $("#work-text").textContent = text;
  }
}

async function loadHome() {
  const q = $("#q").value.trim();
  const folder = S.route.folder || "";
  $("#home-title").textContent = q ? "Search" : folder || "Home";
  $("#now").hidden = !!(q || folder);
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (folder) params.set("folder", folder);
  let list;
  try { list = await getJSON("/api/notes?" + params); } catch { return; }
  renderList(list, q, folder);
}

function renderList(list, q, folder) {
  const box = $("#list");
  if (!list.length) {
    box.innerHTML = `<div class="empty">${
      q ? `Nothing matches “${esc(q)}”.`
        : folder ? "No meetings in this folder yet. Add one with the folder chip on a note."
        : "No meetings yet. Start a recording above, or pull recordings from your Pocket with the ↓ button."
    }</div>`;
    return;
  }
  let out = "", last = "";
  for (const n of list) {
    const d = new Date(n.created_at);
    const label = dayLabel(d);
    if (label !== last) { out += `<div class="day">${esc(label)}</div>`; last = label; }
    const [stText, stCls] = STATUS[n.status] || ["", ""];
    const sub = [n.source === "pocket" ? "Pocket" : "This PC", fmtDur(n.duration_s), n.folder]
      .filter(Boolean).join(" · ");
    const act = n.status === "audio" || n.status === "error"
      ? `<button class="row-act" data-transcribe="${esc(n.id)}">Transcribe</button>` : "";
    out += `<a class="row" href="#/note/${encodeURIComponent(n.id)}">` +
      `<span class="row-icon">${icon(n.source === "pocket" ? "pocket" : "doc")}</span>` +
      `<span class="row-main"><span class="row-title">${esc(n.title || "Untitled meeting")}</span>` +
      `<span class="row-sub">${esc(sub)}</span></span>` +
      (stText ? `<span class="badge ${stCls}">${esc(stText)}</span>` : "") + act +
      `<span class="row-time">${esc(fmtTime(d))}</span></a>`;
  }
  box.innerHTML = out;
}

$("#list").addEventListener("click", async e => {
  const b = e.target.closest("[data-transcribe]");
  if (!b) return;
  e.preventDefault(); e.stopPropagation();
  b.disabled = true; b.textContent = "Queued";
  const r = await send("POST", noteUrl(b.dataset.transcribe, "/transcribe"));
  toast(r.message || "Queued for transcription.");
  refreshSoon();
});

async function startRecording(title) {
  const r = await send("POST", "/api/start", { title });
  if (!r.ok) {
    if (r.status === 409 && r.note_id) location.hash = `#/note/${encodeURIComponent(r.note_id)}`;
    toast(r.message || "Couldn't start recording.");
    return;
  }
  $("#now-title").value = "";
  await poll();
  location.hash = `#/note/${encodeURIComponent(r.note_id)}`;
}
$("#now-start").onclick = () => startRecording($("#now-title").value.trim());
$("#now-title").addEventListener("keydown", e => { if (e.key === "Enter") $("#now-start").click(); });
$("#new-btn").onclick = () => startRecording("");

async function pauseOrResume() {
  const paused = S.status && S.status.state === "paused";
  await send("POST", paused ? "/api/resume" : "/api/pause");
  poll();
}
async function stopRecording() {
  await send("POST", "/api/stop");
  poll();
}
$("#live-pause").onclick = pauseOrResume;
$("#live-stop").onclick = stopRecording;
$("#live-open").onclick = () => {
  if (S.status && S.status.note_id) location.hash = `#/note/${encodeURIComponent(S.status.note_id)}`;
};

$("#pull-btn").onclick = async () => {
  toast("Looking for your Pocket…");
  const r = await send("POST", "/api/usb");
  toast(r.message || (r.ok ? "Done." : "Couldn't read your Pocket."));
  refreshSoon();
};

let qT;
$("#q").addEventListener("input", () => {
  clearTimeout(qT);
  qT = setTimeout(() => {
    // Search covers every meeting, so results always show on Home.
    if (S.route.view === "home" && !S.route.folder) { loadHome(); markNav(); }
    else if ($("#q").value.trim()) location.hash = "#/";
  }, 200);
});

// --- note ---------------------------------------------------------------------
let pending = {}, saveT = null;

function queueSave(fields) {
  Object.assign(pending, fields);
  $("#saved").textContent = "Editing…";
  clearTimeout(saveT);
  saveT = setTimeout(flushNote, 600);
}
async function flushNote() {
  clearTimeout(saveT);
  const id = S.noteId;
  if (!id || !Object.keys(pending).length) return;
  const body = pending;
  pending = {};
  const r = await send("PUT", noteUrl(id), body);
  if (S.noteId === id) $("#saved").textContent = r.ok ? "Saved" : "Couldn't save";
}
function leaveNote() {
  flushNote();
  player.pause();
  player.removeAttribute("src");
  player.load();
  S.noteId = null;
  S.note = null;
  $("#n-transcript").dataset.for = "";
  $("#dock-pill").dataset.mode = "";
}
// Last-chance save when the window closes.
window.addEventListener("pagehide", () => {
  if (S.noteId && Object.keys(pending).length) {
    navigator.sendBeacon(withToken(noteUrl(S.noteId)),
      new Blob([JSON.stringify(pending)], { type: "application/json" }));
    pending = {};
  }
});

function autosize(el) { el.style.height = "auto"; el.style.height = el.scrollHeight + "px"; }

async function openNote(id) {
  const r = await api(noteUrl(id));
  if (!r.ok) { toast("That note no longer exists."); location.hash = "#/"; return; }
  const n = await r.json();
  if (S.route.view !== "note" || S.route.id !== id) return;  // navigated away meanwhile
  const fresh = S.noteId !== id;
  S.noteId = id;
  S.note = n;
  if (fresh) {
    $("#n-title").value = n.title || "";
    $("#n-body").value = n.notes || "";
    $("#saved").textContent = "";
    setTab(n.has_transcript && !n.notes ? "transcript" : "notes");
    if (!n.title && LIVE.includes(n.status)) $("#n-title").focus();
  }
  if (n.has_audio && !player.getAttribute("src")) player.src = withToken(noteUrl(id, "/audio"));
  renderNoteMeta(n);
  renderTranscript(n);
  renderDock();
}

function renderNoteMeta(n) {
  const d = new Date(n.created_at);
  $("#n-date span").textContent = `${dayLabel(d)}, ${fmtTime(d)}`;
  const dur = fmtDur(n.duration_s);
  $("#n-dur").hidden = !dur;
  $("#n-dur span").textContent = dur;
  $("#n-src span").textContent = n.source === "pocket" ? "Pocket" : "This PC";
  renderFolderSelect(n.folder);
  const busy = ["transcribing", "queued", ...LIVE].includes(n.status);
  $("#m-audio").hidden = !n.has_audio;
  $("#m-audio").href = withToken(noteUrl(n.id, "/audio?download=1"));
  $("#m-transcript").hidden = !n.has_transcript;
  $("#m-transcript").href = withToken(noteUrl(n.id, "/transcript?download=1"));
  $("#m-retranscribe").hidden = !n.has_audio || busy;
  $("#m-retranscribe").lastChild.textContent = n.has_transcript ? "Transcribe again" : "Transcribe";
}

function renderFolderSelect(current) {
  const sel = $("#n-folder");
  if (document.activeElement === sel) return;  // don't yank it out from under the user
  const names = S.folders.map(f => f.name);
  if (current && !names.includes(current)) names.push(current);
  sel.innerHTML = `<option value="">${current ? "No folder" : "Add to folder"}</option>` +
    names.map(f => `<option value="${esc(f)}">${esc(f)}</option>`).join("") +
    `<option value="__new__">New folder…</option>`;
  sel.value = current || "";
}
$("#n-folder").onchange = async e => {
  let v = e.target.value;
  e.target.blur();
  if (v === "__new__") {
    v = (prompt("New folder name") || "").trim();
    if (!v) { renderFolderSelect(S.note.folder); return; }
    const r = await send("POST", "/api/folders", { name: v });
    if (!r.ok) { toast(r.message); renderFolderSelect(S.note.folder); return; }
    v = r.name;
  }
  const r = await send("PUT", noteUrl(S.noteId), { folder: v });
  if (r.ok) { S.note.folder = v; $("#saved").textContent = "Saved"; }
  await loadFolders();
};

$("#n-title").addEventListener("input", e => queueSave({ title: e.target.value }));
$("#n-title").addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); setTab("notes"); $("#n-body").focus(); }
});
$("#n-body").addEventListener("input", e => { autosize(e.target); queueSave({ notes: e.target.value }); });

function setTab(t) {
  $$(".seg [data-tab]").forEach(b => b.setAttribute("aria-selected", String(b.dataset.tab === t)));
  $("#tab-notes").hidden = t !== "notes";
  $("#tab-transcript").hidden = t !== "transcript";
  if (t === "notes") autosize($("#n-body"));
}
$$(".seg [data-tab]").forEach(b => { b.onclick = () => setTab(b.dataset.tab); });

// Transcript lines look like "[03:12] Me: words" — drawn as a conversation.
const LINE = /^\[(\d+):(\d{2})\]\s+([^:]{1,40}):\s?(.*)$/;
function renderTranscript(n) {
  const box = $("#n-transcript");
  if (!n.has_transcript) {
    box.dataset.for = "";
    let msg, btn = "";
    if (LIVE.includes(n.status)) msg = "The transcript appears here after you stop recording.";
    else if (n.status === "transcribing") msg = "Transcribing now…";
    else if (n.status === "queued") msg = "Queued. It starts when the current transcription finishes.";
    else if (n.has_audio) {
      msg = n.error || "This recording hasn't been transcribed yet.";
      btn = `<button class="btn primary" data-act="transcribe">${n.error ? "Try again" : "Transcribe now"}</button>`;
    } else msg = n.error || "No recording for this note.";
    box.innerHTML = `<div class="t-empty"><p>${esc(msg)}</p>${btn}</div>`;
    return;
  }
  const key = `${n.id}:${n.transcript.length}`;
  if (box.dataset.for === key) return;  // unchanged — don't redraw under the reader
  box.dataset.for = key;
  const me = (n.me_label || "Me").toLowerCase();
  let out = "", prev = "";
  for (const raw of n.transcript.split("\n")) {
    if (!raw.trim()) continue;
    const m = raw.match(LINE);
    if (!m) { out += `<div class="t-line">${esc(raw)}</div>`; prev = ""; continue; }
    const secs = Number(m[1]) * 60 + Number(m[2]);
    const who = m[3].trim();
    const head = who !== prev ? `<div class="t-who">${esc(who)}</div>` : "";
    prev = who;
    out += `<div class="t-msg ${who.toLowerCase() === me ? "me" : "them"}">${head}` +
      `<div class="t-bubble"><button class="t-ts" data-seek="${secs}" title="Play from here"` +
      `${n.has_audio ? "" : " disabled"}>${esc(m[1].padStart(2, "0"))}:${m[2]}</button>${esc(m[4])}</div></div>`;
  }
  box.innerHTML = out;
}

async function transcribeNote() {
  if (!S.noteId) return;
  const r = await send("POST", noteUrl(S.noteId, "/transcribe"));
  toast(r.message || (r.ok ? "Queued." : "Couldn't queue it."));
  await poll();
  refreshSoon();
}
$("#n-transcript").addEventListener("click", e => {
  if (e.target.closest("[data-act='transcribe']")) { transcribeNote(); return; }
  const ts = e.target.closest("[data-seek]");
  if (ts && player.getAttribute("src")) {
    player.currentTime = Number(ts.dataset.seek);
    player.play();
  }
});

// The dock: recording controls while live, a player afterwards.
function renderDock() {
  const n = S.note, s = S.status || {};
  const dock = $("#dock"), pill = $("#dock-pill");
  if (!n) { dock.hidden = true; return; }
  const live = s.note_id === n.id && LIVE.includes(s.state);
  let mode;
  if (live) mode = "live";
  else if (n.has_audio) mode = `player:${n.status}`;
  else mode = "none";
  dock.hidden = mode === "none";
  if (pill.dataset.mode !== mode) {
    pill.dataset.mode = mode;
    if (live) {
      pill.innerHTML = `<span class="bars" aria-hidden="true"><i></i><i></i><i></i><i></i></span>` +
        `<span class="dock-label" id="d-label"></span><span class="timer" id="d-timer"></span>` +
        `<button class="dock-btn" id="d-pause"></button>` +
        `<button class="dock-btn stop" id="d-stop" title="Stop recording" aria-label="Stop recording">${icon("stop")}</button>`;
    } else if (mode !== "none") {
      let tag = "";
      if (n.status === "transcribing") tag = `<span class="dock-tag"><span class="lamp busy"></span>Transcribing…</span>`;
      else if (n.status === "queued") tag = `<span class="dock-tag"><span class="lamp"></span>Queued</span>`;
      else if (n.status === "audio") tag = `<button class="btn primary sm" id="d-trans">Transcribe</button>`;
      else if (n.status === "error") tag = `<button class="btn sm" id="d-trans">Retry transcription</button>`;
      pill.innerHTML = `<button class="dock-btn" id="d-play" aria-label="Play"></button>` +
        `<input type="range" class="scrub" id="d-scrub" min="0" max="1000" value="0" aria-label="Seek">` +
        `<span class="timer" id="d-time"></span>${tag}`;
    } else pill.innerHTML = "";
  }
  if (live) {
    const paused = s.state === "paused", saving = s.state === "saving";
    $("#d-label").textContent = saving ? "Saving…" : paused ? "Paused" : "Recording";
    $("#d-timer").textContent = clock(s.elapsed);
    const pb = $("#d-pause");
    pb.innerHTML = icon(paused ? "play" : "pause");
    pb.title = paused ? "Resume" : "Pause";
    pb.disabled = saving;
    $("#d-stop").disabled = saving;
  } else if (mode !== "none") syncPlayer();
}

function syncPlayer() {
  const play = $("#d-play");
  if (!play) return;
  play.innerHTML = icon(player.paused ? "play" : "pause");
  play.setAttribute("aria-label", player.paused ? "Play" : "Pause");
  const dur = isFinite(player.duration) && player.duration ? player.duration : (S.note && S.note.duration_s) || 0;
  if (!S.scrubbing) $("#d-scrub").value = dur ? Math.round((player.currentTime / dur) * 1000) : 0;
  $("#d-time").textContent = `${clock(player.currentTime)} / ${clock(dur)}`;
}
["timeupdate", "play", "pause", "loadedmetadata", "ended"].forEach(ev => player.addEventListener(ev, syncPlayer));

$("#dock-pill").addEventListener("click", e => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.id === "d-pause") pauseOrResume();
  else if (b.id === "d-stop") stopRecording();
  else if (b.id === "d-play") { if (player.paused) player.play(); else player.pause(); }
  else if (b.id === "d-trans") transcribeNote();
});
$("#dock-pill").addEventListener("input", e => {
  if (e.target.id !== "d-scrub") return;
  S.scrubbing = true;
  const dur = isFinite(player.duration) ? player.duration : 0;
  if (dur) player.currentTime = (e.target.value / 1000) * dur;
});
$("#dock-pill").addEventListener("change", e => { if (e.target.id === "d-scrub") S.scrubbing = false; });

// "…" menu
function closeMenu() { $("#more-menu").hidden = true; }
$("#more-btn").onclick = e => { e.stopPropagation(); $("#more-menu").hidden = !$("#more-menu").hidden; };
document.addEventListener("click", e => { if (!e.target.closest(".menu-wrap")) closeMenu(); });
$("#m-audio").onclick = closeMenu;
$("#m-transcript").onclick = closeMenu;
$("#m-retranscribe").onclick = () => { closeMenu(); transcribeNote(); };
$("#m-delete").onclick = async () => {
  closeMenu();
  if (!confirm("Delete this note, its recording and its transcript? This can't be undone.")) return;
  const r = await send("DELETE", noteUrl(S.noteId));
  if (!r.ok) { toast(r.message || "Couldn't delete it."); return; }
  pending = {};
  toast("Note deleted.");
  location.hash = "#/";
};

// --- settings -----------------------------------------------------------------
async function loadSettings() {
  let c;
  try { c = await getJSON("/api/config"); } catch { return; }
  S.config = c;
  $$("#view-settings [data-key]").forEach(el => {
    const k = el.dataset.key;
    if (k === "mic_device" || !(k in c)) return;
    if (el.type === "checkbox") el.checked = !!c[k];
    else {
      if (el.tagName === "SELECT" && ![...el.options].some(o => o.value === c[k])) {
        el.insertAdjacentHTML("beforeend", `<option value="${esc(c[k])}">${esc(c[k])}</option>`);
      }
      el.value = c[k] ?? "";
    }
  });
  const key = $("#s-key");
  key.value = "";
  key.placeholder = c.pocket_key_set ? "Saved. Type to replace it" : "pk_…";
  $("#s-root").textContent = `App folder: ${c.app_root}`;
  $("#s-theme").value = themePref();
  loadDevices(c.mic_device);
}

async function loadDevices(selected) {
  const sel = $("#s-mic");
  sel.innerHTML = `<option value="">Finding microphones…</option>`;
  try {
    const d = await getJSON("/api/devices");
    const def = d.default ? `System default (${d.default})` : "System default";
    sel.innerHTML = `<option value="">${esc(def)}</option>` +
      d.mics.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
    if (selected && !d.mics.includes(selected)) {
      sel.insertAdjacentHTML("beforeend", `<option value="${esc(selected)}">${esc(selected)} (not connected)</option>`);
    }
    sel.value = selected || "";
    $("#s-loop").textContent = d.loopback ? `Their side is captured from ${d.loopback}`
      : d.error ? `Audio devices unavailable: ${d.error}` : "Your side of the conversation";
  } catch { sel.innerHTML = `<option value="">System default</option>`; }
}

$("#view-settings").addEventListener("change", async e => {
  const el = e.target.closest("[data-key]");
  if (!el || !S.config) return;
  const k = el.dataset.key;
  const v = el.type === "checkbox" ? el.checked : el.value.trim();
  if (k === "pocket_api_key" && !v) return;
  if (["me_label", "them_label", "recordings_dir"].includes(k) && !v) { el.value = S.config[k]; return; }
  const r = await send("POST", "/api/config", { [k]: v });
  if (!r.ok) { toast("Couldn't save that setting."); return; }
  toast("Saved");
  S.config[k] = v;
  if (k === "pocket_api_key") { el.value = ""; el.placeholder = "Saved. Type to replace it"; }
  if (k === "recordings_dir") { await loadSettings(); refreshSoon(); }
  poll();
});

// --- modals -------------------------------------------------------------------
function openModal(title, body, wide = false) {
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = body;
  $("#modal").classList.toggle("wide", wide);
  $("#modal").hidden = false;
}
function closeModal() { $("#modal").hidden = true; $("#modal-body").innerHTML = ""; }
$("#modal-close").onclick = closeModal;
$("#modal").addEventListener("click", e => { if (e.target.id === "modal") closeModal(); });

async function showPhone() {
  const d = await getJSON("/api/phone");
  openModal("Link your phone",
    (d.svg ? `<div class="qr">${d.svg}</div>` : "") +
    `<div class="qr-url">${esc(d.url)}</div>` +
    `<p class="hint">Scan this with your phone's camera while it's on the same Wi-Fi, then add the page to your home screen. ` +
    `The link includes your access token, so only open it on your own phone.</p>`);
}
async function showLog() {
  const text = await (await api("/api/log")).text();
  openModal("Activity log", `<pre class="log">${esc(text)}</pre>`, true);
  const body = $("#modal-body");
  body.scrollTop = body.scrollHeight;
}
async function quitApp() {
  if (!confirm("Quit Scribe? A recording in progress would be lost.")) return;
  try { await send("POST", "/api/quit"); } catch {}
  $("#offline").textContent = "Scribe has stopped. You can close this window.";
  setTimeout(() => window.close(), 500);
}
$("#phone-btn").onclick = showPhone;
$("#s-phone").onclick = showPhone;
$("#log-btn").onclick = showLog;
$("#s-log").onclick = showLog;
$("#s-quit").onclick = quitApp;

// --- keyboard -----------------------------------------------------------------
document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    openSide();
    $("#q").focus();
    $("#q").select();
  } else if (e.key === "Escape") {
    if (!$("#modal").hidden) closeModal();
    else if (!$("#more-menu").hidden) closeMenu();
    else if (document.activeElement === $("#q") && $("#q").value) {
      $("#q").value = ""; loadHome(); markNav();
    } else closeSide();
  }
});

// --- go -------------------------------------------------------------------------
(async function init() {
  paintIcons();
  applyTheme(themePref());
  renderDate();
  await Promise.all([poll(), loadFolders()]);
  await route();
  setInterval(poll, 1000);
  setInterval(() => { if (S.route.view === "home") loadHome(); }, 20000);
})();
