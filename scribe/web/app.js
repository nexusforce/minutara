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
  sparkle: '<path d="M12 3.5l1.9 4.6 4.6 1.9-4.6 1.9L12 16.5l-1.9-4.6L5.5 10l4.6-1.9z"/><path d="M18.5 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
  up: '<path d="M12 19V6M6.5 11.5 12 6l5.5 5.5"/>',
  speaker: '<path d="M4.5 9.5h3l4.5-4v13l-4.5-4h-3z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>',
  copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7.5a2 2 0 0 0 2 2h2.5"/>',
};
const SOURCE = { pocket: "Pocket", granola: "Granola", pc: "This PC" };
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
  ai: { enabled: false, label: "" }, tab: "notes",
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
  if (prev.view !== S.route.view || prev.id !== S.route.id) resetChat();
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
  const t = s.transcriber || {}, sm = s.summarizer || {}, gr = s.granola || {};
  S.ai = s.ai || { enabled: false };
  const importing = ["listing", "importing"].includes(gr.state);
  document.body.dataset.rec = s.state;
  document.body.classList.toggle("working",
    (!!t.busy || !!sm.busy || importing) && !LIVE.includes(s.state));
  const cloud = $("#cloud-state");
  const sends = s.push_to_pocket || (S.ai.enabled && !S.ai.local);
  cloud.textContent = S.ai.enabled ? `AI: ${S.ai.label}` : s.push_to_pocket ? "Pocket cloud on" : "Local only";
  cloud.classList.toggle("cloud", !!sends);
  $$(".askbox").forEach(f => { f.hidden = !S.ai.enabled; });
  renderNow();
  if (S.route.view === "note") renderDock();

  // Something changed (a recording saved, a transcript finished): refresh.
  const sig = [s.state, s.note_id, t.name, t.queued, t.message, s.message,
               sm.id, sm.queued, sm.message, gr.state, Math.floor((gr.done || 0) / 10)].join("|");
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
  const t = s.transcriber || {}, sm = s.summarizer || {}, gr = s.granola || {};
  const lines = [];
  if (t.busy) {
    let text = `Transcribing “${t.title || t.name}”`;
    if (t.queued) text += ` · ${t.queued} more queued`;
    if (!t.model_ready) text += " · the first run downloads the speech model, so this one takes a while";
    lines.push(text);
  }
  if (sm.busy) lines.push(`Writing AI notes for “${sm.title}”` + (sm.queued ? ` · ${sm.queued} more queued` : ""));
  if (["listing", "importing"].includes(gr.state)) {
    lines.push(gr.state === "listing" ? "Finding your Granola meetings…"
      : `Importing from Granola · ${gr.done} of ${gr.total}`);
  }
  $("#work").hidden = !lines.length;
  $("#work-text").textContent = lines.join("  ·  ");
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
    let [stText, stCls] = STATUS[n.status] || ["", ""];
    if (!stText && n.ai_status) [stText, stCls] = [n.ai_status === "writing" ? "Writing AI notes…" : "AI notes queued", "busy"];
    const sub = [SOURCE[n.source] || "This PC", fmtDur(n.duration_s), n.folder]
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

async function pullPocket() {
  toast("Looking for your Pocket…");
  const r = await send("POST", "/api/usb");
  toast(r.message || (r.ok ? "Done." : "Couldn't read your Pocket."));
  refreshSoon();
}
$("#pull-btn").onclick = pullPocket;

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
    requestAnimationFrame(() => autosize($("#n-title")));
    $("#n-body").value = n.notes || "";
    $("#saved").textContent = "";
    resetChat();
    setTab(n.summary ? "ai" : n.has_transcript && !n.notes ? "transcript" : "notes");
    if (!n.title && LIVE.includes(n.status)) $("#n-title").focus();
  }
  if (n.has_audio && !player.getAttribute("src")) player.src = withToken(noteUrl(id, "/audio"));
  renderNoteMeta(n);
  renderSummary(n);
  renderTranscript(n);
  renderDock();
}

// AI notes tab — Granola's "enhanced notes", from the provider in Settings.
function renderSummary(n) {
  const head = $("#ai-head"), box = $("#n-summary");
  const busy = n.ai_status;
  const meta = n.summary_meta || {};
  const fromGranola = meta.provider === "granola";
  if (n.summary) {
    const when = meta.at ? dayLabel(new Date(meta.at)) : "";
    const by = fromGranola ? "Imported from Granola" : `Written by ${meta.model || meta.provider || "AI"}`;
    head.innerHTML = `<span>${esc(by)}${when ? " · " + esc(when) : ""}</span><span class="grow"></span>` +
      (busy ? `<span class="dock-tag"><span class="lamp busy"></span>Rewriting…</span>`
        : (S.ai.enabled ? `<button class="btn sm" data-ai-act="gen">${icon("refresh")}Regenerate</button>` : "")) +
      `<button class="btn sm" data-ai-act="copy">${icon("copy")}Copy</button>`;
    if (box.dataset.for !== n.id + ":" + n.summary.length) {
      box.innerHTML = md(n.summary);
      box.dataset.for = n.id + ":" + n.summary.length;
    }
    if (n.summary_error && !busy) head.insertAdjacentHTML("beforeend",
      `<div class="now-hint err" style="width:100%">Last attempt failed: ${esc(n.summary_error)}</div>`);
    return;
  }
  head.innerHTML = "";
  box.dataset.for = "";
  let msg, btn = "";
  if (busy) msg = busy === "writing" ? "Writing AI notes…" : "Queued. AI notes start when the current one finishes.";
  else if (!S.ai.enabled) {
    msg = "AI notes turn your transcript and rough notes into clean meeting notes, like Granola. " +
          "Pick Claude, OpenAI, Gemini or a private local model in Settings.";
    btn = `<a class="btn primary" href="#/settings" data-ai-act="setup">Set up AI notes</a>`;
  } else if (!n.has_transcript && !(n.notes || "").trim()) {
    msg = LIVE.includes(n.status) ? "AI notes are written after the recording is transcribed."
      : "There's nothing to write notes from yet: no transcript or typed notes.";
  } else {
    msg = n.summary_error ? `Last attempt failed: ${n.summary_error}` : "No AI notes for this meeting yet.";
    btn = `<button class="btn primary" data-ai-act="gen">${icon("sparkle")}Generate AI notes</button>`;
  }
  box.innerHTML = `<div class="t-empty"><p>${esc(msg)}</p>${btn}</div>`;
}

$("#tab-ai").addEventListener("click", async e => {
  const b = e.target.closest("[data-ai-act]");
  if (!b || !S.note) return;
  if (b.dataset.aiAct === "setup") { setTimeout(() => $("#s-ai").scrollIntoView(), 300); return; }
  if (b.dataset.aiAct === "copy") { copyText(S.note.summary, b); return; }
  const r = await send("POST", noteUrl(S.noteId, "/summarize"));
  toast(r.message || (r.ok ? "Writing AI notes…" : "Couldn't start AI notes."));
  await poll();
  refreshSoon();
});

async function copyText(text, btn) {
  try { await navigator.clipboard.writeText(text); }
  catch {  // plain-HTTP pages don't always get the clipboard API
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand("copy"); ta.remove();
  }
  if (btn) { const old = btn.innerHTML; btn.textContent = "Copied"; setTimeout(() => { btn.innerHTML = old; }, 1200); }
}

// A small, safe Markdown renderer: headings, nested lists, bold/italic,
// inline code and http(s) links. Input is escaped before any markup is added.
function mdInline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])[*_]([^*_\s][^*_]*?)[*_](?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/\u0001/g, "*");
}
function md(src) {
  const lines = String(src || "").replace(/\r/g, "").split("\n");
  let out = "", para = [];
  const stack = [];
  const flush = () => { if (para.length) { out += `<p>${mdInline(para.join(" "))}</p>`; para = []; } };
  const close = (to = -1) => {
    while (stack.length && stack[stack.length - 1].indent > to) out += `</li></${stack.pop().type}>`;
  };
  for (let raw of lines) {
    raw = raw.replace(/\\\*/g, "\u0001").replace(/\\([^\w\s])/g, "$1");  // markdown escapes like \~
    if (!raw.trim()) { flush(); continue; }
    const h = raw.match(/^\s{0,3}(#{1,6})\s+(.*)$/);
    if (h) {
      flush(); close();
      const lvl = Math.min(h[1].length + 1, 4);
      out += `<h${lvl}>${mdInline(h[2])}</h${lvl}>`;
      continue;
    }
    const li = raw.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
    if (li) {
      flush();
      const indent = li[1].replace(/\t/g, "  ").length, type = /\d/.test(li[2]) ? "ol" : "ul";
      close(indent);
      const top = stack[stack.length - 1];
      if (top && top.indent === indent) out += "</li><li>";
      else { out += `<${type}><li>`; stack.push({ type, indent }); }
      out += mdInline(li[3]);
      continue;
    }
    if (stack.length && /^\s{2,}\S/.test(raw)) { out += `<div class="li-more">${mdInline(raw.trim())}</div>`; continue; }
    close();
    para.push(raw.trim());
  }
  flush(); close();
  return out;
}

function renderNoteMeta(n) {
  const d = new Date(n.created_at);
  $("#n-date span").textContent = `${dayLabel(d)}, ${fmtTime(d)}`;
  const dur = fmtDur(n.duration_s);
  $("#n-dur").hidden = !dur;
  $("#n-dur span").textContent = dur;
  $("#n-src span").textContent = SOURCE[n.source] || "This PC";
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

$("#n-title").addEventListener("input", e => {
  e.target.value = e.target.value.replace(/\n/g, " ");  // a title is one line, even if pasted
  autosize(e.target);
  queueSave({ title: e.target.value });
});
window.addEventListener("resize", () => { if (S.noteId) autosize($("#n-title")); });
$("#n-title").addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); setTab("notes"); $("#n-body").focus(); }
});
$("#n-body").addEventListener("input", e => { autosize(e.target); queueSave({ notes: e.target.value }); });

function setTab(t) {
  S.tab = t;
  $$(".seg [data-tab]").forEach(b => b.setAttribute("aria-selected", String(b.dataset.tab === t)));
  $("#tab-ai").hidden = t !== "ai";
  $("#tab-notes").hidden = t !== "notes";
  $("#tab-transcript").hidden = t !== "transcript";
  if (t === "notes") autosize($("#n-body"));
}
$$(".seg [data-tab]").forEach(b => { b.onclick = () => setTab(b.dataset.tab); });

// Transcript lines look like "[03:12] Me: words" — or, from Granola, plain
// "Kate Stobbe: words" — and are drawn as a conversation.
const LINE = /^(?:\[(\d+):(\d{2})\]\s+)?([^:\[\]]{1,60}):\s?(.*)$/;
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
  if (!n.transcript.trim()) {
    box.innerHTML = `<div class="t-empty"><p>No speech was found in this recording. Play it back to check it has sound;
      if it's silent or static, use <a href="#/settings">Settings → Test mic</a> to find a microphone that works.</p>
      ${n.has_audio ? `<button class="btn" data-act="transcribe">Transcribe again</button>` : ""}</div>`;
    return;
  }
  const me = (n.me_label || "Me").toLowerCase();
  let out = "", prev = "";
  for (const raw of n.transcript.split("\n")) {
    if (!raw.trim()) continue;
    const m = raw.match(LINE);
    if (!m) { out += `<div class="t-line">${esc(raw)}</div>`; prev = ""; continue; }
    const who = m[3].trim();
    const head = who !== prev ? `<div class="t-who">${esc(who)}</div>` : "";
    prev = who;
    const ts = m[1] === undefined ? "" :
      `<button class="t-ts" data-seek="${Number(m[1]) * 60 + Number(m[2])}" title="Play from here"` +
      `${n.has_audio ? "" : " disabled"}>${esc(m[1].padStart(2, "0"))}:${m[2]}</button>`;
    out += `<div class="t-msg ${who.toLowerCase() === me ? "me" : "them"}">${head}` +
      `<div class="t-bubble">${ts}${esc(m[4])}</div></div>`;
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
  pill.hidden = mode === "none";
  dock.hidden = pill.hidden && !S.ai.enabled;
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

// --- Ask anything ---------------------------------------------------------------
const chat = { history: [], busy: false };
const CHIPS = {
  note: [["Action items", "List every action item from this meeting with its owner."],
         ["Follow-up email", "Write a short, friendly follow-up email to the attendees: thank them, recap the decisions and list each action item with its owner."],
         ["Decisions", "What was decided in this meeting, and why?"]],
  home: [["Recent to-dos", "List my open to-dos from recent meetings, grouped by meeting."],
         ["This week", "Summarize what happened in my meetings this week."],
         ["My commitments", "What did I commit to in recent meetings?"]],
};
const chatScope = () => (S.route.view === "note" ? "note" : "home");

function resetChat() {
  chat.history = [];
  $("#chat-body").innerHTML = "";
  $("#chat").hidden = true;
}
function openChat() {
  const scope = chatScope();
  $("#chat-title").textContent = scope === "note" ? "Ask about this meeting" : "Ask about your meetings";
  $("#chat-prov").textContent = S.ai.label || "";
  $("#chat-chips").innerHTML = chat.history.length || chat.busy ? "" : CHIPS[scope].map(([label, prompt]) =>
    `<button class="ask-chip" data-prompt="${esc(prompt)}">${esc(label)}</button>`).join("");
  $("#chat").hidden = false;
}
async function askQuestion(q) {
  q = (q || "").trim();
  if (!q || chat.busy) return;
  if (!S.ai.enabled) { toast("Pick an AI provider in Settings first."); location.hash = "#/settings"; return; }
  const scope = chatScope();
  chat.busy = true;
  openChat();
  const body = $("#chat-body");
  body.insertAdjacentHTML("beforeend", `<div class="chat-q">${esc(q)}</div>`);
  const a = document.createElement("div");
  a.className = "chat-a wait";
  a.textContent = scope === "note" ? "Reading the meeting…" : "Reading your meetings…";
  body.appendChild(a);
  body.scrollTop = body.scrollHeight;
  $$(".ask-go").forEach(b => { b.disabled = true; });
  const r = await send("POST", "/api/ask", {
    question: q, note_id: scope === "note" ? S.noteId : null, history: chat.history });
  chat.busy = false;
  $$(".ask-go").forEach(b => { b.disabled = false; });
  if (!r.ok) { a.className = "chat-a err"; a.textContent = r.message || "Couldn't get an answer."; return; }
  chat.history.push({ role: "user", content: q }, { role: "assistant", content: r.answer });
  const srcs = scope === "home" && r.sources && r.sources.length
    ? `<div class="chat-src">Looked at: ${r.sources.slice(0, 8).map(s =>
        `<a href="#/note/${encodeURIComponent(s.id)}">${esc(s.title)}</a>`).join(", ")}` +
      `${r.sources.length > 8 ? ` and ${r.sources.length - 8} more` : ""}</div>` : "";
  a.className = "chat-a md";
  a.innerHTML = md(r.answer) + srcs +
    `<div class="chat-tools"><button class="btn sm" data-copy>${icon("copy")}Copy</button></div>`;
  a.querySelector("[data-copy]").onclick = e => copyText(r.answer, e.currentTarget);
  body.scrollTop = body.scrollHeight;
}
$$(".askbox").forEach(f => {
  f.addEventListener("submit", e => {
    e.preventDefault();
    const input = f.querySelector("input");
    askQuestion(input.value);
    input.value = "";
  });
  f.querySelector(".ask-chip").onclick = e => askQuestion(e.currentTarget.dataset.prompt);
});
$("#chat-chips").addEventListener("click", e => {
  const b = e.target.closest("[data-prompt]");
  if (b) askQuestion(b.dataset.prompt);
});
$("#chat-close").onclick = () => { $("#chat").hidden = true; };
$("#chat-clear").onclick = () => { if (!chat.busy) { resetChat(); openChat(); } };

// --- settings -----------------------------------------------------------------
async function loadSettings() {
  let c;
  try { c = await getJSON("/api/config"); } catch { return; }
  S.config = c;
  $$("#view-settings [data-key]").forEach(el => {
    const k = el.dataset.key;
    if (k === "mic_device" || k === "speaker_device" || !(k in c)) return;
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
  if (!S.aiDefaults) { try { S.aiDefaults = await getJSON("/api/ai/defaults"); } catch {} }
  renderAI();
  renderGranola();
  loadDevices(c.mic_device, c.speaker_device);
  pollSettings();
}
const reloadConfig = async () => { S.config = await getJSON("/api/config"); };
async function saveConfig(body, msg = "Saved") {
  const r = await send("POST", "/api/config", body);
  if (!r.ok) { toast("Couldn't save that setting."); return false; }
  if (msg) toast(msg);
  return true;
}

// AI provider settings. Each provider keeps its own key, model and limits,
// so switching back and forth doesn't lose anything.
const LIMITS = [
  ["max_tokens", "Max output tokens", "number"],
  ["timeout", "Timeout (seconds)", "number"],
  ["max_input_chars", "Max input characters", "number", "Longer meetings are summarized in parts."],
];
const AI_FIELDS = {
  anthropic: {
    main: [
      ["api_key", "API key", "key", "From console.anthropic.com → API Keys."],
      ["model", "Model", "model", "claude-opus-5-5 writes the best notes. claude-haiku-5-5 is the cheapest."],
      ["effort", "Effort", ["", "low", "medium", "high", "xhigh", "max"],
        "How hard Claude thinks. medium suits meeting notes. Blank leaves it out (for models without effort)."],
    ],
    adv: [
      ["fallbacks", "Refusal fallback", ["default", ""],
        "default: if Claude declines, Anthropic retries on a fallback model. Blank turns it off."],
      ["base_url", "API address", "text", "Blank uses Anthropic's normal address."], ...LIMITS],
  },
  openai: {
    main: [
      ["api_key", "API key", "key", "From platform.openai.com → API keys."],
      ["model", "Model", "model", "Use Fetch models to see what your key can use."],
    ],
    adv: [
      ["api_style", "API", ["responses", "chat_completions"], "OpenAI's Responses API, or the older Chat Completions."],
      ["token_param", "Output-limit parameter", ["max_completion_tokens", "max_tokens", ""],
        "Chat Completions only. Blank sends none."],
      ["base_url", "API address", "text", "Blank uses OpenAI's normal address."], ...LIMITS],
  },
  gemini: {
    main: [
      ["api_key", "API key", "key", "From aistudio.google.com → Get API key."],
      ["model", "Model", "model", "gemini-flash-latest always points at Google's newest Flash model."],
    ],
    adv: [
      ["base_url", "API address", "text"],
      ["generate_path", "Generate path", "text", "{model} is replaced with the model name."],
      ["models_path", "Models path", "text"], ...LIMITS],
  },
  ollama: {
    main: [
      ["base_url", "Ollama address", "text",
        "Install Ollama from ollama.com, then pull a model, e.g. ollama pull llama3.2:3b"],
      ["model", "Model", "model", "Fetch models lists the models you've pulled."],
    ],
    adv: [
      ["num_ctx", "Context window (tokens)", "number", "Bigger reads more of a meeting at once but needs more memory."],
      ["timeout", "Timeout (seconds)", "number"],
      ["max_input_chars", "Max input characters", "number", "Longer meetings are summarized in parts."]],
  },
  compatible: {
    main: [
      ["base_url", "Server address", "text",
        "For example http://localhost:1234/v1 (LM Studio) or https://openrouter.ai/api/v1"],
      ["api_key", "API key", "key", "Leave blank if the server doesn't need one."],
      ["model", "Model", "model"],
    ],
    adv: [
      ["api_style", "API", ["chat_completions", "responses"]],
      ["token_param", "Output-limit parameter", ["max_tokens", "max_completion_tokens", ""]], ...LIMITS],
  },
};
const AI_WHERE = {
  off: "Off: nothing is sent anywhere for AI.",
  anthropic: "Transcripts and notes go to Anthropic when AI notes are written or you ask a question.",
  openai: "Transcripts and notes go to OpenAI when AI notes are written or you ask a question.",
  gemini: "Transcripts and notes go to Google when AI notes are written or you ask a question.",
  ollama: "Runs on this computer. Nothing leaves it.",
  compatible: "Transcripts and notes go to the server you set below.",
};

function aiControl(p, [key, label, type, hint], value, envKey) {
  const attrs = `data-aip="${key}" data-p="${p}" aria-label="${esc(label)}"`;
  let ctl;
  if (type === "key") {
    const ph = value ? "Saved. Type to replace it" : envKey ? "Using the key from your environment" : "Paste your key";
    ctl = `<div class="btns"><input class="field wide" type="password" ${attrs} autocomplete="off" spellcheck="false" placeholder="${ph}">` +
      (value ? `<button class="btn ghost sm" data-ai-clear="${p}">Remove</button>` : "") + `</div>`;
  } else if (type === "model") {
    ctl = `<div class="btns"><input class="field" list="ai-models-${p}" ${attrs} value="${esc(value)}" spellcheck="false">` +
      `<datalist id="ai-models-${p}"></datalist><button class="btn sm" data-ai-fetch="${p}">Fetch models</button></div>`;
  } else if (Array.isArray(type)) {
    const opts = type.includes(value) ? type : [...type, value];
    ctl = `<select class="sel" ${attrs}>${opts.map(o =>
      `<option value="${esc(o)}"${o === value ? " selected" : ""}>${esc(o || "(none)")}</option>`).join("")}</select>`;
  } else {
    ctl = `<input class="field ${type === "number" ? "num" : "wide"}" type="${type === "number" ? "number" : "text"}" ` +
      `${attrs} value="${esc(value)}" spellcheck="false">`;
  }
  return `<div class="srow"><div class="stext"><b>${esc(label)}</b>${hint ? `<small>${esc(hint)}</small>` : ""}</div>${ctl}</div>`;
}

function renderAI() {
  const c = S.config, p = c.ai.provider;
  $("#ai-provider").value = p;
  $("#ai-where").textContent = AI_WHERE[p] || "";
  $("#ai-where").classList.toggle("warn", p !== "off" && p !== "ollama");
  $$("[data-ai-on]").forEach(el => { el.hidden = p === "off"; });
  $$("[data-ai]").forEach(el => {
    const k = el.dataset.ai;
    if (el.type === "checkbox") el.checked = !!c.ai[k];
    else el.value = c.ai[k] ?? "";
  });
  if (S.aiDefaults) {
    $("#ai-sp").placeholder = S.aiDefaults.summary_prompt;
    $("#ai-cp").placeholder = S.aiDefaults.chat_prompt;
  }
  const box = $("#ai-fields");
  if (!AI_FIELDS[p]) { box.innerHTML = ""; return; }
  const pc = c.ai.providers[p] || {};
  const value = k => (k === "api_key" ? pc.api_key_set : pc[k] ?? "");
  const env = (c.ai_env_keys || {})[p];
  box.innerHTML = AI_FIELDS[p].main.map(f => aiControl(p, f, value(f[0]), env)).join("") +
    `<div class="srow"><div class="stext"><b>Check the connection</b><small>Sends a tiny test message.</small></div>` +
    `<button class="btn" data-ai-test="${p}">Test</button></div>` +
    `<div class="ai-result" id="ai-result" hidden></div>` +
    `<details class="adv"><summary>Advanced (addresses &amp; limits)</summary>` +
    AI_FIELDS[p].adv.map(f => aiControl(p, f, value(f[0]), env)).join("") + `</details>`;
}
function showAIResult(text, kind) {
  const el = $("#ai-result");
  if (!el) return;
  el.hidden = false;
  el.className = `ai-result ${kind || ""}`;
  el.textContent = text;
}

// Granola import settings and progress.
function renderGranola() {
  const g = S.config.granola;
  $$("[data-gr]").forEach(el => {
    const k = el.dataset.gr;
    if (k === "api_key") { el.value = ""; el.placeholder = g.api_key_set ? "Saved. Type to replace it" : "grn_…"; }
    else el.value = g[k] ?? "";
  });
  $$("[data-gm]").forEach(el => { el.hidden = el.dataset.gm !== g.method; });
}
const onThisComputer = () => ["127.0.0.1", "localhost", "::1"].includes(location.hostname);

async function pollSettings() {
  if (S.route.view !== "settings") return;
  let g, p;
  try {
    [g, p] = await Promise.all([getJSON("/api/granola/status"), getJSON("/api/pocket/status")]);
  } catch { return; }
  const running = ["listing", "importing"].includes(g.state);
  $("#gr-account").textContent = g.signed_in
    ? `Signed in${g.signed_in_at ? " since " + dayLabel(new Date(g.signed_in_at)) : ""}.`
    : onThisComputer() ? "Not signed in." : "Not signed in. Sign in from Scribe on your computer.";
  $("#gr-signin").textContent = g.signed_in ? "Sign in again" : "Sign in";
  $("#gr-signin").disabled = !onThisComputer();
  $("#gr-signout").hidden = !g.signed_in;
  $("#gr-import").hidden = running;
  $("#gr-stop").hidden = !running;
  $("#gr-bar").hidden = !running && g.state !== "done";
  $("#gr-bar span").style.width = g.total ? `${Math.round((g.done / g.total) * 100)}%` : running ? "4%" : "100%";
  let msg = g.message;
  if (running && g.current) msg += ` Now: “${g.current}”.`;
  if (!msg) {
    msg = g.imported_total ? `${g.imported_total} meetings imported so far. Running it again only adds what's new.`
      : "Copies titles, attendees, Granola's AI notes, your notes and full transcripts. Running it again only adds what's new.";
  }
  $("#gr-msg").textContent = msg;
  const errs = $("#gr-errors");
  errs.hidden = !g.errors || !g.errors.length;
  errs.innerHTML = (g.errors || []).map(x => `<div>${esc(x)}</div>`).join("");

  $("#pk-usb").textContent = p.usb ? `Connected at ${p.usb}`
    : "Not connected. Plug it in and turn on USB Mode in the Pocket app.";
  $("#pk-cloud-msg").textContent = (p.cloud_job && p.cloud_job.message) ||
    "Downloads recordings from your Pocket account (needs Pocket cloud and a key).";
  $("#pk-cloud").disabled = (p.cloud_job || {}).state === "running";
}
setInterval(pollSettings, 2000);

// A short chime through the computer's speakers, for the speakers test to hear.
function playChime(seconds) {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const notes = [523.25, 659.25, 783.99, 1046.5];
    const t0 = ctx.currentTime + 0.15;
    for (let i = 0; i < Math.floor(seconds * 3); i++) {
      const t = t0 + i / 3, o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "sine";
      o.frequency.value = notes[i % notes.length];
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.3, t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.3);
      o.connect(g).connect(ctx.destination);
      o.start(t); o.stop(t + 0.32);
    }
    setTimeout(() => ctx.close(), (seconds + 1) * 1000);
  } catch {}
}

async function testAudio(kind) {
  const btn = kind === "mic" ? $("#t-mic") : $("#t-spk");
  const other = kind === "mic" ? $("#t-spk") : $("#t-mic");
  const label = btn.innerHTML;
  const msg = $("#t-msg");
  btn.disabled = other.disabled = true;
  btn.textContent = kind === "mic" ? "Speak now…" : "Listening…";
  msg.className = "";
  msg.textContent = kind === "mic"
    ? "Recording 4 seconds: say a sentence at your normal volume."
    : "Playing a chime and listening for it…";
  $("#t-result").hidden = true;
  const req = send("POST", "/api/devices/test",
    { kind, mic: $("#s-mic").value, speaker: $("#s-spk").value, seconds: 4 });
  if (kind === "speakers") playChime(3.4);
  const r = await req;
  btn.disabled = other.disabled = false;
  btn.innerHTML = label;
  const good = r.ok && r.verdict === "good";
  msg.textContent = r.message || (good ? "Works." : "The test didn't work.");
  msg.className = good ? "ok" : "warn";
  if (!r.device) return;
  // Level bar: -60 dB (silence) .. 0 dB (full scale), on the loudest moment.
  const pk = r.peak_db == null ? -100 : r.peak_db;
  const lv = $("#t-level");
  lv.style.width = Math.max(0, Math.min(100, (pk + 60) / 60 * 100)) + "%";
  lv.className = r.verdict === "good" ? "" : r.verdict === "low" || r.verdict === "quiet" ? "low" : "bad";
  const db = v => v == null ? "silent" : `${Math.round(v)} dB`;
  $("#t-facts").textContent = `${r.device} · ${r.host_api}, ${r.rate / 1000} kHz, ${r.channels} ch · `
    + `loudest ${db(r.peak_db)}, average ${db(r.rms_db)} · sent ${r.sent_s}s of audio in ${r.seconds}s`;
  $("#t-audio").src = r.audio || "";
  $("#t-result").hidden = false;
  if (r.audio) $("#t-audio").play().catch(() => {});
}

$("#view-settings").addEventListener("click", async e => {
  if (e.target.closest("#t-mic")) { testAudio("mic"); return; }
  if (e.target.closest("#t-spk")) { testAudio("speakers"); return; }
  const fetchBtn = e.target.closest("[data-ai-fetch]");
  if (fetchBtn) {
    e.preventDefault();
    const p = fetchBtn.dataset.aiFetch;
    fetchBtn.disabled = true; fetchBtn.textContent = "Fetching…";
    const r = await send("POST", "/api/ai/models", { provider: p });
    fetchBtn.disabled = false; fetchBtn.textContent = "Fetch models";
    if (!r.ok) { showAIResult(r.message, "err"); return; }
    $(`#ai-models-${p}`).innerHTML = r.models.map(m => `<option value="${esc(m)}">`).join("");
    showAIResult(r.models.length
      ? `${r.models.length} models available. Click the Model box to pick one.`
      : "The provider listed no models.", r.models.length ? "ok" : "err");
    return;
  }
  const testBtn = e.target.closest("[data-ai-test]");
  if (testBtn) {
    testBtn.disabled = true; testBtn.textContent = "Testing…";
    showAIResult("Sending a test message…", "");
    const r = await send("POST", "/api/ai/test", { provider: testBtn.dataset.aiTest });
    testBtn.disabled = false; testBtn.textContent = "Test";
    showAIResult(r.message || (r.ok ? "Connected." : "The test failed."), r.ok ? "ok" : "err");
    return;
  }
  const clearBtn = e.target.closest("[data-ai-clear]");
  if (clearBtn) {
    if (!confirm("Remove the saved API key?")) return;
    await saveConfig({ ai: { providers: { [clearBtn.dataset.aiClear]: { api_key: null } } } }, "Key removed");
    await reloadConfig(); renderAI(); poll();
    return;
  }
  const id = e.target.closest("button") && e.target.closest("button").id;
  if (id === "gr-signin") {
    const w = window.open("about:blank", "_blank");  // open now, or pop-up blockers object
    const r = await send("POST", "/api/granola/signin");
    if (!r.ok) { if (w) w.close(); toast(r.message || "Couldn't start Granola sign-in."); return; }
    if (w) { w.location = r.url; toast("Finish signing in to Granola in the new window."); }
    else location.href = r.url;
  } else if (id === "gr-signout") {
    await send("POST", "/api/granola/signout"); toast("Signed out of Granola."); pollSettings();
  } else if (id === "gr-import" || id === "gr-reimport") {
    if (id === "gr-reimport" && !confirm("Refresh every imported meeting from Granola? Notes you've typed in Scribe are kept.")) return;
    const r = await send("POST", "/api/granola/import", { refresh: id === "gr-reimport" });
    toast(r.message || (r.ok ? "Import started." : "Couldn't start the import."));
    pollSettings(); poll();
  } else if (id === "gr-stop") {
    await send("POST", "/api/granola/stop"); toast("Stopping after the current meeting…");
  } else if (id === "pk-pull") {
    pullPocket();
  } else if (id === "pk-cloud") {
    const r = await send("POST", "/api/pocket/cloud-import", { days: Number($("#pk-days").value) || 30 });
    toast(r.message || (r.ok ? "Importing…" : "Couldn't start."));
    pollSettings();
  }
});

function fillSelect(sel, items, selected, defLabel) {
  sel.innerHTML = `<option value="">${esc(defLabel)}</option>` +
    items.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
  if (selected && !items.includes(selected)) {
    sel.insertAdjacentHTML("beforeend", `<option value="${esc(selected)}">${esc(selected)} (not connected)</option>`);
  }
  sel.value = selected || "";
}

async function loadDevices(selected, speaker) {
  const sel = $("#s-mic");
  sel.innerHTML = `<option value="">Finding microphones…</option>`;
  $("#s-spk").innerHTML = `<option value="">Finding speakers…</option>`;
  try {
    const d = await getJSON("/api/devices");
    fillSelect($("#s-spk"), d.speakers || [], speaker,
      d.speaker_default ? `Windows default (${d.speaker_default})` : "Windows default");
    const def = d.default ? `System default (${d.default})` : "System default";
    sel.innerHTML = `<option value="">${esc(def)}</option>` +
      d.mics.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
    const full = selected && !d.mics.includes(selected)
      && d.mics.find(m => m.toLowerCase().startsWith(selected.toLowerCase()));
    if (full) selected = full;
    if (selected && !d.mics.includes(selected)) {
      sel.insertAdjacentHTML("beforeend", `<option value="${esc(selected)}">${esc(selected)} (not connected)</option>`);
    }
    sel.value = selected || "";
    $("#s-loop").textContent = d.error ? `Audio devices unavailable: ${d.error}` : "Your side of the conversation";
  } catch {
    sel.innerHTML = `<option value="">System default</option>`;
    $("#s-spk").innerHTML = `<option value="">Windows default</option>`;
  }
}

$("#view-settings").addEventListener("change", async e => {
  if (!S.config) return;
  if (e.target.id === "ai-provider") {
    const p = e.target.value;
    if (await saveConfig({ ai: { provider: p } }, p === "off" ? "AI turned off" : "AI provider saved")) {
      await reloadConfig(); renderAI(); poll();
    }
    return;
  }
  const aip = e.target.closest("[data-aip]");
  if (aip) {
    const k = aip.dataset.aip;
    const v = aip.type === "number" ? Number(aip.value) : aip.value.trim();
    if (k === "api_key" && !v) return;
    if (await saveConfig({ ai: { providers: { [aip.dataset.p]: { [k]: v } } } })) {
      if (k === "api_key") { await reloadConfig(); renderAI(); }
      else S.config.ai.providers[aip.dataset.p][k] = v;
      poll();
    }
    return;
  }
  const aik = e.target.closest("[data-ai]");
  if (aik) {
    const k = aik.dataset.ai;
    const v = aik.type === "checkbox" ? aik.checked : aik.type === "number" ? Number(aik.value) : aik.value;
    if (await saveConfig({ ai: { [k]: v } }, k.endsWith("_prompt") && !v ? "Using the built-in prompt" : "Saved")) {
      S.config.ai[k] = v;
    }
    return;
  }
  const gr = e.target.closest("[data-gr]");
  if (gr) {
    const k = gr.dataset.gr, v = gr.value.trim();
    if (k === "api_key" && !v) return;
    if (await saveConfig({ granola: { [k]: v } })) { await reloadConfig(); renderGranola(); pollSettings(); }
    return;
  }
  const el = e.target.closest("[data-key]");
  if (!el) return;
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
    else if (!$("#chat").hidden) $("#chat").hidden = true;
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
