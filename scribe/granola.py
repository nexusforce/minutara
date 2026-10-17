"""
granola.py — bring your Granola history into Scribe, so nothing is lost when
you cancel.

Two official ways in, both talking straight from this computer to Granola:

  mcp  "Sign in with Granola". Granola's MCP server (https://mcp.granola.ai/mcp)
       with the standard MCP OAuth flow: discovery (RFC 9728 / RFC 8414),
       dynamic client registration (RFC 7591) and PKCE. Works on the plans
       that include Granola's MCP access, with full history and transcripts.
  api  Granola's Personal API with a grn_… key (Business/Enterprise plans).

Each meeting becomes a Scribe note with its title, date, attendees, folder,
your own notes, Granola's AI notes and the transcript. The raw responses are
also saved under granola_archive/, so even a field Scribe doesn't show yet is
kept. Importing again skips meetings already imported (so an interrupted run
resumes); "re-import" refreshes them.

Endpoints, plan scope and the start date are all settings (config "granola").
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import secrets
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests

from . import notes
from .config import GRANOLA_AUTH_PATH, Config
from .log import get

log = get("scribe.granola")

HEADERS = {"User-Agent": "Scribe/1.0 (local meeting notes)"}
MCP_PROTOCOL = "2025-06-18"


class GranolaError(RuntimeError):
    pass


class AuthRequired(GranolaError):
    pass


class NotReady(GranolaError):
    """The note exists but Granola won't serve it yet (e.g. still processing)."""


# --- stored sign-in -----------------------------------------------------------

def load_auth() -> dict:
    try:
        return json.loads(GRANOLA_AUTH_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_auth(data: dict) -> None:
    tmp = GRANOLA_AUTH_PATH.with_name(GRANOLA_AUTH_PATH.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, GRANOLA_AUTH_PATH)


def clear_auth() -> None:
    GRANOLA_AUTH_PATH.unlink(missing_ok=True)


# --- OAuth for the MCP server ---------------------------------------------------

_PENDING: dict[str, dict] = {}   # state → in-flight sign-in (lives in this process)


def _get_json(url: str) -> dict | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.ok and "json" in r.headers.get("Content-Type", "json"):
            return r.json()
    except (requests.RequestException, ValueError):
        pass
    return None


def _well_known(base: str, name: str) -> list[str]:
    """RFC 8414-style candidates: path-inserted first, then root."""
    u = urlparse(base)
    origin = f"{u.scheme}://{u.netloc}"
    path = u.path.rstrip("/")
    out = []
    if path:
        out.append(f"{origin}/.well-known/{name}{path}")
    out.append(f"{origin}/.well-known/{name}")
    if path:
        out.append(f"{base.rstrip('/')}/.well-known/{name}")
    return out


def discover(mcp_url: str) -> dict:
    """Find the authorization server for the MCP endpoint."""
    resource_meta_url = None
    try:  # an unauthenticated call should answer 401 and point at its metadata
        r = requests.post(mcp_url, json={"jsonrpc": "2.0", "id": 0, "method": "ping"},
                          headers={**HEADERS, "Accept": "application/json, text/event-stream"},
                          timeout=20)
        m = re.search(r'resource_metadata="([^"]+)"', r.headers.get("WWW-Authenticate", ""))
        if m:
            resource_meta_url = m.group(1)
    except requests.RequestException as e:
        raise GranolaError(f"Couldn't reach Granola at {mcp_url} ({e.__class__.__name__}).")

    prm = None
    for url in ([resource_meta_url] if resource_meta_url else []) + \
            _well_known(mcp_url, "oauth-protected-resource"):
        prm = _get_json(url)
        if prm and prm.get("authorization_servers"):
            break
        prm = None
    issuer = (prm or {}).get("authorization_servers", [None])[0] or \
        f"{urlparse(mcp_url).scheme}://{urlparse(mcp_url).netloc}"

    as_meta = None
    for url in _well_known(issuer, "oauth-authorization-server") + \
            _well_known(issuer, "openid-configuration"):
        as_meta = _get_json(url)
        if as_meta and as_meta.get("authorization_endpoint") and as_meta.get("token_endpoint"):
            break
        as_meta = None
    if not as_meta:
        raise GranolaError("Couldn't find Granola's sign-in server. "
                           "Check the MCP address in Settings → Import from Granola.")
    return {"resource": (prm or {}).get("resource") or mcp_url,
            "scopes": (prm or {}).get("scopes_supported") or [],
            "issuer": as_meta.get("issuer") or issuer, "as": as_meta}


def begin_sign_in(cfg: Config, redirect_uri: str) -> str:
    """Prepare a PKCE sign-in and return the URL to open in the browser."""
    g = cfg.granola
    meta = discover(g["mcp_url"])
    asm = meta["as"]
    auth = load_auth()

    client = None
    if g.get("client_id"):
        client = {"client_id": g["client_id"]}
    elif auth.get("client", {}).get("redirect_uri") == redirect_uri and \
            auth.get("issuer") == meta["issuer"]:
        client = auth["client"]          # registered on an earlier sign-in
    elif asm.get("registration_endpoint"):
        r = requests.post(asm["registration_endpoint"], headers=HEADERS, timeout=20, json={
            "client_name": "Scribe",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        })
        if not r.ok:
            raise GranolaError(f"Granola refused to register Scribe ({r.status_code}): {r.text[:200]}")
        client = {**r.json(), "redirect_uri": redirect_uri}
    else:
        raise GranolaError("Granola's sign-in server doesn't let new apps register. "
                           "Use the API key method, or set a client ID under Advanced.")

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    _PENDING[state] = {"verifier": verifier, "client": client, "meta": meta,
                       "redirect_uri": redirect_uri, "at": time.time()}
    params = {
        "response_type": "code", "client_id": client["client_id"],
        "redirect_uri": redirect_uri, "code_challenge": challenge,
        "code_challenge_method": "S256", "state": state, "resource": meta["resource"],
    }
    if meta["scopes"]:
        params["scope"] = " ".join(meta["scopes"])
    log.info("granola sign-in started (issuer %s)", meta["issuer"])
    return asm["authorization_endpoint"] + ("&" if "?" in asm["authorization_endpoint"] else "?") \
        + urlencode(params)


def _token_request(token_endpoint: str, client: dict, data: dict) -> dict:
    data = {**data, "client_id": client["client_id"]}
    auth = None
    if client.get("client_secret"):
        if client.get("token_endpoint_auth_method") == "client_secret_post":
            data["client_secret"] = client["client_secret"]
        else:
            auth = (client["client_id"], client["client_secret"])
    r = requests.post(token_endpoint, data=data, auth=auth, headers=HEADERS, timeout=30)
    if not r.ok:
        raise AuthRequired(f"Granola sign-in failed ({r.status_code}): {r.text[:200]}")
    return r.json()


def finish_sign_in(code: str, state: str) -> None:
    pending = _PENDING.pop(state, None)
    if not pending or time.time() - pending["at"] > 900:
        raise GranolaError("That sign-in link expired. Start again from Scribe's settings.")
    meta, client = pending["meta"], pending["client"]
    tok = _token_request(meta["as"]["token_endpoint"], client, {
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": pending["redirect_uri"], "code_verifier": pending["verifier"],
        "resource": meta["resource"],
    })
    save_auth({
        "issuer": meta["issuer"], "token_endpoint": meta["as"]["token_endpoint"],
        "resource": meta["resource"], "client": client,
        "access_token": tok["access_token"], "refresh_token": tok.get("refresh_token", ""),
        "expires_at": time.time() + float(tok.get("expires_in") or 3600),
        "signed_in_at": datetime.now().isoformat(timespec="seconds"),
    })
    log.info("granola sign-in complete")


def access_token(force_refresh: bool = False) -> str:
    auth = load_auth()
    if not auth.get("access_token"):
        raise AuthRequired("Sign in to Granola first.")
    if force_refresh or auth.get("expires_at", 0) - 60 < time.time():
        if not auth.get("refresh_token"):
            raise AuthRequired("Your Granola sign-in expired. Sign in again.")
        tok = _token_request(auth["token_endpoint"], auth["client"], {
            "grant_type": "refresh_token", "refresh_token": auth["refresh_token"],
            "resource": auth.get("resource", ""),
        })
        auth.update(access_token=tok["access_token"],
                    refresh_token=tok.get("refresh_token") or auth["refresh_token"],
                    expires_at=time.time() + float(tok.get("expires_in") or 3600))
        save_auth(auth)
    return auth["access_token"]


# --- MCP client (Streamable HTTP transport) -------------------------------------------

def _sse_messages(resp: requests.Response):
    data: list[str] = []
    for raw in resp.iter_lines(decode_unicode=True):
        line = raw if raw is not None else ""
        if not line:
            if data:
                yield "\n".join(data)
                data = []
            continue
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield "\n".join(data)


class MCP:
    def __init__(self, url: str):
        self.url = url
        self.session_id = None
        self.protocol = MCP_PROTOCOL
        self._id = 0
        self._ready = False

    def _post(self, msg: dict, want_id=None, _retried=False):
        headers = {**HEADERS, "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "Authorization": f"Bearer {access_token(force_refresh=_retried)}"}
        if self._ready:
            headers["MCP-Protocol-Version"] = self.protocol
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        try:
            r = requests.post(self.url, json=msg, headers=headers, timeout=180, stream=True)
        except requests.RequestException as e:
            raise GranolaError(f"Couldn't reach Granola ({e.__class__.__name__}).")
        if r.status_code == 401 and not _retried:
            return self._post(msg, want_id, _retried=True)
        if r.status_code == 401:
            raise AuthRequired("Granola didn't accept the sign-in. Sign in again.")
        if r.status_code == 404 and self.session_id and not _retried:
            self.session_id, self._ready = None, False     # session expired
            self.initialize()
            return self._post(msg, want_id, _retried=True)
        if not r.ok:
            raise GranolaError(f"Granola returned {r.status_code}: {r.text[:300]}")
        if r.headers.get("Mcp-Session-Id"):
            self.session_id = r.headers["Mcp-Session-Id"]
        if want_id is None:
            r.close()
            return None
        if "text/event-stream" in r.headers.get("Content-Type", ""):
            for data in _sse_messages(r):
                try:
                    m = json.loads(data)
                except ValueError:
                    continue
                if m.get("id") == want_id:
                    return m
            raise GranolaError("Granola closed the connection without answering.")
        return r.json()

    def initialize(self) -> None:
        self._id += 1
        res = self._post({"jsonrpc": "2.0", "id": self._id, "method": "initialize", "params": {
            "protocolVersion": MCP_PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "Scribe", "version": "1.0"}}}, want_id=self._id)
        if "error" in res:
            raise GranolaError(f"Granola MCP: {res['error'].get('message')}")
        self.protocol = res.get("result", {}).get("protocolVersion") or MCP_PROTOCOL
        self._ready = True
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, tool: str, args: dict) -> str:
        if not self._ready:
            self.initialize()
        self._id += 1
        res = self._post({"jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                          "params": {"name": tool, "arguments": args}}, want_id=self._id)
        if "error" in res:
            raise GranolaError(f"Granola {tool}: {res['error'].get('message')}")
        result = res.get("result") or {}
        text = "\n".join(c.get("text", "") for c in result.get("content", [])
                         if c.get("type") == "text")
        if result.get("isError"):
            raise GranolaError(f"Granola {tool}: {text[:300]}")
        return text


# --- parsing --------------------------------------------------------------------

_MEETING_RE = re.compile(r"<meeting\s+([^>]*?)>(.*?)</meeting>", re.S)
_ATTR_RE = re.compile(r'([\w:-]+)="([^"]*)"')
_SECTION_RE = re.compile(r"<(\w+)>(.*?)</\1>", re.S)
SUMMARY_TAGS = ("summary", "enhanced_notes", "ai_notes", "ai_summary")
NOTES_TAGS = ("private_notes", "my_notes", "user_notes", "notes")


def parse_meetings(text: str) -> list[dict]:
    """Parse list_meetings / get_meetings output."""
    out = []
    for m in _MEETING_RE.finditer(text):
        attrs = {k: html.unescape(v) for k, v in _ATTR_RE.findall(m.group(1))}
        body = m.group(2)
        sections = {k: html.unescape(v).strip() for k, v in _SECTION_RE.findall(body)}
        people = [ln.strip() for ln in sections.get("known_participants", "").splitlines() if ln.strip()]
        out.append({
            "id": attrs.get("id", ""),
            "title": attrs.get("title", ""),
            "meeting_at": attrs.get("meeting_at") or attrs.get("created_at", ""),
            "url": attrs.get("url", ""),
            "folders": [html.unescape(t) for t in re.findall(r'<folder\s[^>]*?title="([^"]*)"', body)],
            "attendees": people,
            "summary": next((sections[t] for t in SUMMARY_TAGS if sections.get(t)), ""),
            "my_notes": next((sections[t] for t in NOTES_TAGS if sections.get(t)), ""),
            "attrs": attrs,
            "sections": sections,
            "block": m.group(0),
        })
    return out


def parse_json_payload(text: str) -> dict:
    """Tool output = an advisory line, then JSON. Take the JSON."""
    start = text.find("{")
    if start < 0:
        raise GranolaError("Unexpected transcript format from Granola.")
    return json.loads(text[start:])


def _speaker(label: str, me: str, them: str) -> str:
    low = label.lower().strip()
    if low.startswith("microphone"):
        return me
    m = re.match(r"system audio\s*\((.+)\)$", label.strip(), re.I)
    if m:
        return m.group(1).strip()
    if low in ("system audio", "system", "speaker", "speakers"):
        return them
    return label.strip()


def mcp_transcript_lines(raw: str, me: str, them: str) -> list[str]:
    """'System audio (Kate): words\\n\\nMicrophone: words' → Scribe lines."""
    lines: list[str] = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        block = block.strip()
        if not block:
            continue
        m = re.match(r"^([^:\n]{1,80}):\s*(.*)$", block, re.S)
        if m:
            lines.append(f"{_speaker(m.group(1), me, them)}: {' '.join(m.group(2).split())}")
        elif lines:
            lines[-1] += " " + " ".join(block.split())
        else:
            lines.append(" ".join(block.split()))
    return lines


def _iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def api_transcript_lines(segments: list, me: str, them: str) -> list[str]:
    """Personal API segments → '[mm:ss] Speaker: text' lines."""
    t0 = next((_iso(s.get("start_time", "")) for s in segments if _iso(s.get("start_time", ""))), None)
    out = []
    for s in segments:
        sp = s.get("speaker") or {}
        name = sp.get("name") or sp.get("diarization_label")
        src = (sp.get("source") or "").lower()
        who = name or (me if src == "microphone" else them)
        text = " ".join(str(s.get("text", "")).split())
        if not text:
            continue
        t = _iso(s.get("start_time", ""))
        if t and t0:
            secs = max(0, int((t - t0).total_seconds()))
            out.append(f"[{secs // 60:02d}:{secs % 60:02d}] {who}: {text}")
        else:
            out.append(f"{who}: {text}")
    return out


# --- sources ----------------------------------------------------------------------

def _windows(since: date, until: date, days: int = 120):
    start = since
    while start <= until:
        end = min(start + timedelta(days=days - 1), until)
        yield start, end
        start = end + timedelta(days=1)


class MCPSource:
    method = "mcp"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.mcp = MCP(cfg.granola["mcp_url"])
        self.details: dict[str, dict] = {}

    def list(self) -> list[dict]:
        g = self.cfg.granola
        try:
            since = date.fromisoformat(g.get("since") or "2018-01-01")
        except ValueError:
            since = date(2018, 1, 1)
        found: dict[str, dict] = {}
        for start, end in _windows(since, date.today()):
            args = {"time_range": "custom", "custom_start": start.isoformat(),
                    "custom_end": end.isoformat()}
            if g.get("scope", "mine") == "mine":
                args["involvement"] = {"captured_by_me": True, "listed_as_participant": True}
            for m in parse_meetings(self.mcp.call("list_meetings", args)):
                if m["id"]:
                    found[m["id"]] = m
        return sorted(found.values(), key=lambda m: m["meeting_at"])

    def prefetch(self, batch: list[dict]) -> None:
        """get_meetings takes up to 10 ids at once."""
        ids = [m["id"] for m in batch if m["id"] not in self.details]
        if not ids:
            return
        try:
            text = self.mcp.call("get_meetings", {"meeting_ids": ids})
            for d in parse_meetings(text):
                self.details[d["id"]] = d
        except AuthRequired:
            raise
        except GranolaError as e:
            log.warning("batch details failed (%s); fetching one by one", e)

    def fetch(self, meta: dict, me: str, them: str) -> dict:
        d = self.details.pop(meta["id"], None)
        if d is None:
            text = self.mcp.call("get_meetings", {"meeting_ids": [meta["id"]]})
            found = parse_meetings(text)
            if not found:
                raise NotReady("Granola returned no details for this meeting.")
            d = found[0]
        transcript, error, parts = "", "", []
        offset = 0
        try:
            for _ in range(200):  # 200 × 80k chars is far beyond any meeting
                payload = parse_json_payload(self.mcp.call(
                    "get_meeting_transcript", {"meeting_id": meta["id"], "offset": offset}))
                parts.append(payload)
                transcript += payload.get("transcript") or ""
                if not payload.get("truncated") or payload.get("next_offset") in (None, offset):
                    break
                offset = int(payload["next_offset"])
        except AuthRequired:
            raise
        except GranolaError as e:
            error = str(e)
        return {
            "id": meta["id"], "title": d["title"] or meta["title"],
            "meeting_at": d["meeting_at"] or meta["meeting_at"],
            "url": d["url"] or meta["url"],
            "folders": d["folders"] or meta["folders"],
            "attendees": d["attendees"] or meta["attendees"],
            "summary": d["summary"], "my_notes": d["my_notes"],
            "transcript_lines": mcp_transcript_lines(transcript, me, them) if transcript else [],
            "transcript_error": error,
            "raw": {"list": meta.get("attrs"), "details_block": d.get("block"),
                    "transcript_parts": [{k: v for k, v in p.items() if k != "transcript"}
                                         for p in parts],
                    "transcript_text": transcript},
        }


class APISource:
    method = "api"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.base = cfg.granola["api_base"].rstrip("/")
        key = cfg.granola.get("api_key") or os.environ.get("GRANOLA_API_KEY", "")
        if not key:
            raise AuthRequired("Add your Granola API key first.")
        self.headers = {**HEADERS, "Authorization": f"Bearer {key}"}
        self._last = 0.0

    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        for attempt in range(6):
            wait = 0.25 - (time.time() - self._last)  # stay under ~5 requests/second
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                r = requests.get(self.base + path, params=params, headers=self.headers, timeout=60)
            except requests.RequestException as e:
                if attempt == 5:
                    raise GranolaError(f"Couldn't reach Granola's API ({e.__class__.__name__}).")
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 401:
                raise AuthRequired("Granola rejected the API key.")
            if r.status_code == 403:
                raise AuthRequired("This Granola key can't use the API (it needs a Business or "
                                   "Enterprise plan). Use “Sign in with Granola” instead.")
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(float(r.headers.get("Retry-After") or 2 ** attempt))
                continue
            return r
        return r

    def list(self) -> list[dict]:
        out, cursor = [], None
        since = self.cfg.granola.get("since") or ""
        while True:
            params = {"page_size": 30}
            if since:
                params["created_after"] = since
            if cursor:
                params["cursor"] = cursor
            r = self._get("/notes", params)
            if not r.ok:
                raise GranolaError(f"Granola's API returned {r.status_code}: {r.text[:200]}")
            data = r.json()
            for n in data.get("notes", []):
                out.append({"id": n["id"], "title": n.get("title") or "",
                            "meeting_at": n.get("created_at") or "", "url": n.get("web_url") or "",
                            "folders": [], "attendees": [], "attrs": n})
            cursor = data.get("cursor")
            if not data.get("hasMore") or not cursor:
                return sorted(out, key=lambda m: m["meeting_at"])

    def prefetch(self, batch: list[dict]) -> None:
        pass

    def fetch(self, meta: dict, me: str, them: str) -> dict:
        error = ""
        r = self._get(f"/notes/{meta['id']}", {"include": "transcript"})
        if r.status_code == 413:  # transcript too large to return inline
            error = "Granola's API said the transcript is too large to download."
            r = self._get(f"/notes/{meta['id']}")
        if r.status_code == 404:
            raise NotReady("Granola hasn't finished this note yet (no AI summary), so its API won't return it.")
        if not r.ok:
            raise GranolaError(f"Granola's API returned {r.status_code}: {r.text[:200]}")
        n = r.json()
        cal = n.get("calendar_event") or {}
        folders = []
        for f in n.get("folder_membership") or []:
            folders.append(f.get("name") or f.get("title") if isinstance(f, dict) else str(f))
        attendees = [a.get("name") or a.get("email") for a in n.get("attendees") or [] if isinstance(a, dict)]
        return {
            "id": meta["id"], "title": n.get("title") or meta["title"],
            "meeting_at": cal.get("scheduled_start_time") or cal.get("start_time")
            or n.get("created_at") or meta["meeting_at"],
            "url": n.get("web_url") or "",
            "folders": [f for f in folders if f],
            "attendees": [a for a in attendees if a],
            "summary": n.get("summary_markdown") or n.get("summary_text") or "",
            "my_notes": n.get("notes_markdown") or n.get("notes") or "",
            "transcript_lines": api_transcript_lines(n.get("transcript") or [], me, them),
            "transcript_error": error,
            "raw": n,
        }


# --- writing into Scribe ---------------------------------------------------------------

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def note_id_for(granola_id: str, url: str = "") -> str:
    """One id per meeting whichever method imported it: prefer the UUID."""
    m = _UUID_RE.search(url or "") or _UUID_RE.search(granola_id)
    key = m.group(0).lower() if m else re.sub(r"[^A-Za-z0-9_-]", "", granola_id)
    return f"granola-{key}"


def _local_iso(value: str) -> str:
    dt = _iso(value)
    if dt is None:
        return datetime.now().astimezone().isoformat(timespec="seconds")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().isoformat(timespec="seconds")


def write_note(cfg: Config, rec: dict, method: str) -> str:
    """Create or refresh the Scribe note for one Granola meeting."""
    nid = note_id_for(rec["id"], rec.get("url", ""))
    tpath = cfg.transcripts_dir / f"{nid}.txt"
    if rec["transcript_lines"]:
        tpath.write_text("\n".join(rec["transcript_lines"]) + "\n", encoding="utf-8")

    folder = rec["folders"][0] if rec["folders"] else ""
    if folder:
        folder = notes.clean_folder(folder)
        if folder:
            notes.add_folder(cfg, folder)
    fields = {
        "title": rec["title"] or "Untitled meeting",
        "created_at": _local_iso(rec["meeting_at"]),
        "source": "granola",
        "attendees": rec["attendees"],
        "summary": rec["summary"],
        "summary_meta": {"provider": "granola", "model": "Granola",
                         "at": datetime.now().isoformat(timespec="seconds")} if rec["summary"] else {},
        "summary_error": "",
        "granola": {"id": rec["id"], "url": rec.get("url", ""), "method": method,
                    "complete": True, "transcript_error": rec.get("transcript_error", ""),
                    "imported_at": datetime.now().isoformat(timespec="seconds")},
    }
    existing = notes.load(cfg, nid)
    if existing:
        if not existing.get("notes"):
            fields["notes"] = rec["my_notes"]
        if existing.get("folder"):
            fields.pop("folder", None)
        else:
            fields["folder"] = folder
        notes.update(cfg, nid, **fields)
    else:
        notes.create(cfg, nid, notes=rec["my_notes"], folder=folder, **fields)

    cfg.archive_dir.mkdir(parents=True, exist_ok=True)
    (cfg.archive_dir / f"{nid}.json").write_text(json.dumps({
        "method": method, "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "granola_id": rec["id"], "url": rec.get("url", ""), "raw": rec.get("raw"),
    }, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return nid


# --- the import job ----------------------------------------------------------------------

class Importer:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.cancel = threading.Event()
        self.status = {"state": "idle", "message": "", "total": 0, "done": 0,
                       "imported": 0, "skipped": 0, "failed": 0, "current": "", "errors": []}

    def snapshot(self) -> dict:
        with self.lock:
            s = dict(self.status)
            s["errors"] = list(self.status["errors"][-50:])
            return s

    def _set(self, **kw) -> None:
        with self.lock:
            self.status.update(kw)

    def start(self, refresh: bool = False) -> tuple[bool, str]:
        with self.lock:
            if self.status["state"] in ("listing", "importing"):
                return False, "An import is already running."
            self.cancel.clear()
            self.status = {"state": "listing", "message": "Finding your Granola meetings…",
                           "total": 0, "done": 0, "imported": 0, "skipped": 0, "failed": 0,
                           "current": "", "errors": [], "refresh": refresh,
                           "method": self.cfg.granola.get("method", "mcp")}
            self.thread = threading.Thread(target=self._run, args=(refresh,), daemon=True)
            self.thread.start()
            return True, "Import started."

    def stop(self) -> None:
        self.cancel.set()

    def _run(self, refresh: bool) -> None:
        cfg = self.cfg
        method = cfg.granola.get("method", "mcp")
        me, them = cfg.me_label or "Me", cfg.them_label or "Them"
        try:
            source = MCPSource(cfg) if method == "mcp" else APISource(cfg)
            metas = source.list()
            self._set(state="importing", total=len(metas),
                      message=f"Importing {len(metas)} meeting{'s' if len(metas) != 1 else ''}…")
            log.info("granola import: %d meetings via %s", len(metas), method)
            todo = []
            for meta in metas:
                existing = notes.load(cfg, note_id_for(meta["id"], meta.get("url", "")))
                if existing and (existing.get("granola") or {}).get("complete") and not refresh:
                    with self.lock:
                        self.status["skipped"] += 1
                        self.status["done"] += 1
                else:
                    todo.append(meta)
            for i in range(0, len(todo), 10):
                batch = todo[i:i + 10]
                if self.cancel.is_set():
                    break
                source.prefetch(batch)
                for meta in batch:
                    if self.cancel.is_set():
                        break
                    self._set(current=meta["title"])
                    try:
                        rec = source.fetch(meta, me, them)
                        write_note(cfg, rec, method)
                        with self.lock:
                            self.status["imported"] += 1
                            if rec.get("transcript_error"):
                                self.status["errors"].append(
                                    f"{meta['title']}: imported without a transcript "
                                    f"({rec['transcript_error']})")
                    except AuthRequired:
                        raise
                    except Exception as e:
                        log.warning("granola: %s failed: %s", meta["title"], e)
                        with self.lock:
                            self.status["failed"] += 1
                            self.status["errors"].append(f"{meta['title']}: {e}")
                    finally:
                        with self.lock:
                            self.status["done"] += 1
            s = self.snapshot()
            if self.cancel.is_set():
                msg = f"Stopped. {s['imported']} imported so far — run it again to continue."
            else:
                msg = (f"Done. {s['imported']} imported, {s['skipped']} already here"
                       + (f", {s['failed']} failed" if s["failed"] else "") + ".")
            self._set(state="done", current="", message=msg)
            log.info("granola import finished: %s", msg)
        except AuthRequired as e:
            self._set(state="needs_auth", current="", message=str(e))
        except Exception as e:
            log.exception("granola import failed")
            self._set(state="error", current="", message=str(e))
