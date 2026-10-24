"""One local server impersonating every outside service Minutara talks to.

Shapes follow each provider's documented API (and, for Granola MCP, the exact
text format returned by the real server in this session). Every request is
recorded in CALLS so tests can assert on headers, paths and bodies.
"""
import base64
import hashlib
import json
import threading
import time
from urllib.parse import urlencode

from flask import Flask, Response, jsonify, redirect, request

app = Flask("fake_cloud")
CALLS: list[dict] = []
STATE = {"port": 0, "mcp_401_once": False, "granola_429_once": True, "anthropic_refuse": False,
         "codes": {}, "clients": {}, "refreshes": 0}


def base():
    return f"http://127.0.0.1:{STATE['port']}"


@app.before_request
def record():
    CALLS.append({"method": request.method, "path": request.path, "args": dict(request.args),
                  "headers": {k.lower(): v for k, v in request.headers.items()},
                  "json": request.get_json(silent=True), "form": dict(request.form)})


def reply_for(system: str, user: str) -> str:
    if "Reply with exactly: OK" in system:
        return "OK"
    if "condensing one part" in system:
        return f"- condensed part ({len(user)} chars)"
    if "meeting notes" in system:
        return "# Budget\n\n- Cut events by **8%**\n  - travel flat\n\n# Next steps\n\n- **Draft board note** (Me)"
    return f"ANSWER using {len(user)} chars of context"


# --- Anthropic Messages API (streaming) -----------------------------------------
def _sse(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


@app.post("/anthropic/v1/messages")
def anthropic_messages():
    if request.headers.get("x-api-key") != "sk-ant-good":
        return jsonify({"type": "error", "error": {"type": "authentication_error",
                                                   "message": "invalid x-api-key"}}), 401
    body = request.get_json()
    if body["model"] == "claude-nope":
        return jsonify({"type": "error", "error": {"type": "not_found_error", "message": "model"}}), 404
    system = body.get("system") or ""
    user = body["messages"][-1]["content"]
    text = reply_for(system, user)
    stop = "refusal" if STATE["anthropic_refuse"] else "end_turn"
    msg = {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
           "content": [], "stop_reason": None, "stop_sequence": None,
           "usage": {"input_tokens": 10, "output_tokens": 1}}
    events = [{"type": "message_start", "message": msg}]
    if stop != "refusal":
        events += [{"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                   {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
                   {"type": "content_block_stop", "index": 0}]
    events += [{"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                "usage": {"output_tokens": 5}},
               {"type": "message_stop"}]
    return Response(_sse(events), mimetype="text/event-stream")


@app.get("/anthropic/v1/models")
def anthropic_models():
    data = [{"type": "model", "id": m, "display_name": m, "created_at": "2026-01-01T00:00:00Z"}
            for m in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")]
    return jsonify({"data": data, "has_more": False, "first_id": data[0]["id"], "last_id": data[-1]["id"]})


# --- OpenAI (and OpenAI-compatible) -------------------------------------------------
def _openai_auth():
    return request.headers.get("Authorization") in ("Bearer sk-good", "Bearer not-needed")


@app.post("/openai/v1/responses")
def openai_responses():
    if not _openai_auth():
        return jsonify({"error": {"message": "Incorrect API key", "type": "invalid_request_error"}}), 401
    b = request.get_json()
    user = b["input"][-1]["content"] if isinstance(b["input"], list) else b["input"]
    text = reply_for(b.get("instructions") or "", user)
    return jsonify({"id": "resp_1", "object": "response", "created_at": 0, "status": "completed",
                    "model": b["model"], "output": [{"type": "message", "id": "m1", "status": "completed",
                    "role": "assistant", "content": [{"type": "output_text", "text": text, "annotations": []}]}],
                    "parallel_tool_calls": False, "tool_choice": "auto", "tools": []})


@app.post("/openai/v1/chat/completions")
def openai_chat():
    if not _openai_auth():
        return jsonify({"error": {"message": "Incorrect API key"}}), 401
    b = request.get_json()
    system = next((m["content"] for m in b["messages"] if m["role"] == "system"), "")
    text = reply_for(system, b["messages"][-1]["content"])
    return jsonify({"id": "c1", "object": "chat.completion", "created": 0, "model": b["model"],
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": text}}]})


@app.get("/openai/v1/models")
def openai_models():
    return jsonify({"object": "list", "data": [
        {"id": m, "object": "model", "created": 0, "owned_by": "x"} for m in ("gpt-6.1-sol", "gpt-6-luna")]})


# --- Gemini -------------------------------------------------------------------------
@app.post("/gemini/v1beta/models/<path:model>")
def gemini_generate(model):
    if request.headers.get("x-goog-api-key") != "g-good":
        return jsonify({"error": {"code": 400, "message": "API key not valid", "status": "INVALID_ARGUMENT"}}), 400
    b = request.get_json()
    text = reply_for(b["systemInstruction"]["parts"][0]["text"], b["contents"][-1]["parts"][0]["text"])
    return jsonify({"candidates": [{"content": {"role": "model", "parts": [
        {"text": "thinking…", "thought": True}, {"text": text}]}, "finishReason": "STOP"}]})


@app.get("/gemini/v1beta/models")
def gemini_models():
    return jsonify({"models": [
        {"name": "models/gemini-flash-latest", "supportedGenerationMethods": ["generateContent", "countTokens"]},
        {"name": "models/text-embedding", "supportedGenerationMethods": ["embedContent"]}]})


# --- Ollama ---------------------------------------------------------------------------
@app.post("/ollama/api/chat")
def ollama_chat():
    b = request.get_json()
    if b["model"] != "llama3.2:3b":
        return jsonify({"error": f"model '{b['model']}' not found"}), 404
    text = reply_for(b["messages"][0]["content"], b["messages"][-1]["content"])
    return jsonify({"model": b["model"], "done": True,
                    "message": {"role": "assistant", "content": f"<think>hmm</think>{text}"}})


@app.get("/ollama/api/tags")
def ollama_tags():
    return jsonify({"models": [{"name": "llama3.2:3b"}, {"name": "qwen3:4b"}]})


# --- Granola: OAuth + MCP ----------------------------------------------------------------
@app.get("/.well-known/oauth-protected-resource/mcp")
def prm():
    return jsonify({"resource": base() + "/mcp", "authorization_servers": [base() + "/auth"],
                    "scopes_supported": ["openid", "offline_access"]})


@app.get("/.well-known/oauth-authorization-server/auth")
def as_meta():
    b = base()
    return jsonify({"issuer": b + "/auth", "authorization_endpoint": b + "/auth/authorize",
                    "token_endpoint": b + "/auth/token", "registration_endpoint": b + "/auth/register",
                    "code_challenge_methods_supported": ["S256"]})


@app.post("/auth/register")
def register():
    b = request.get_json()
    cid = f"cid_{len(STATE['clients']) + 1}"
    STATE["clients"][cid] = b
    return jsonify({"client_id": cid, **b}), 201


@app.get("/auth/authorize")
def authorize():
    a = request.args
    assert a["client_id"] in STATE["clients"], "unknown client"
    assert a["code_challenge_method"] == "S256" and a["resource"] == base() + "/mcp"
    code = f"code_{len(STATE['codes']) + 1}"
    STATE["codes"][code] = {"challenge": a["code_challenge"], "redirect": a["redirect_uri"],
                            "client": a["client_id"]}
    return redirect(f"{a['redirect_uri']}?{urlencode({'code': code, 'state': a['state']})}")


@app.post("/auth/token")
def token():
    f = request.form
    if f["grant_type"] == "authorization_code":
        c = STATE["codes"].pop(f["code"], None)
        if not c:
            return jsonify({"error": "invalid_grant"}), 400
        want = base64.urlsafe_b64encode(hashlib.sha256(f["code_verifier"].encode()).digest()).rstrip(b"=").decode()
        if want != c["challenge"] or f["redirect_uri"] != c["redirect"] or f["client_id"] != c["client"]:
            return jsonify({"error": "invalid_grant", "error_description": "PKCE/redirect mismatch"}), 400
        return jsonify({"access_token": "at_1", "refresh_token": "rt_1", "expires_in": 3600, "token_type": "Bearer"})
    if f["grant_type"] == "refresh_token" and f["refresh_token"] == "rt_1":
        STATE["refreshes"] += 1
        return jsonify({"access_token": f"at_r{STATE['refreshes']}", "refresh_token": "rt_1", "expires_in": 3600})
    return jsonify({"error": "invalid_grant"}), 400


MEETINGS = [
    {"id": "11111111-aaaa-4bbb-8ccc-000000000001", "title": "Board prep: Q4 budget &amp; hiring",
     "at": "2025-12-03T10:02:00.000-05:00", "folder": None,
     "summary": "# Budget\n\n- Cut events by 8%\n- S\\&amp;P Global asked for \\~12 guides", "notes": "",
     "transcript": "Microphone: Let&apos;s start.\n\nSystem audio (Alex Rivera): Budget is down 8%.\n\nSystem audio: Somebody else.",
     "denied": False},
    {"id": "11111111-aaaa-4bbb-8ccc-000000000002", "title": "Customer call: onboarding",
     "at": "2026-05-11T14:00:00.000-04:00", "folder": "Client calls",
     "summary": "# Feedback\n\n- Setup guide was clear", "notes": "- ask about SSO\n- send deck",
     "transcript": "System audio (Dana Lee): The guide was clear.\n\nMicrophone: Great, anything confusing?",
     "denied": False},
    {"id": "11111111-aaaa-4bbb-8ccc-000000000003", "title": "Private 1:1",
     "at": "2026-10-16T11:31:03.050-04:00", "folder": None,
     "summary": "# Career\n\n- Growth plan", "notes": "", "transcript": "", "denied": True},
]


def _meeting_xml(m, detail):
    folders = (f'<folders>\n      <folder id="f1" title="{m["folder"]}" visibility="workspace" '
               f'audience="everyone in the workspace" role="owner" />\n    </folders>') if m["folder"] else "<folders />"
    out = (f'<meeting id="{m["id"]}" title="{m["title"]}" date="x" meeting_at="{m["at"]}" '
           f'created_at="{m["at"]}" updated_at="{m["at"]}" captured_by_me="true" url="https://notes.granola.ai/d/{m["id"]}">\n'
           f"  <known_participants>\n  Armando (note creator) from Hechavarria &lt;a@h.org&gt;\n  </known_participants>\n"
           f"    {folders}\n")
    if detail:
        out += f"  \n  <summary>\n{m['summary']}\n</summary>\n"
        if m["notes"]:
            out += f"  <private_notes>\n{m['notes']}\n</private_notes>\n"
    return out + "</meeting>\n"


def _tool_text(name, args):
    if name == "list_meetings":
        lo, hi = args["custom_start"], args["custom_end"]
        hits = [m for m in MEETINGS if lo <= m["at"][:10] <= hi]
        assert args.get("involvement") == {"captured_by_me": True, "listed_as_participant": True}
        return ('<requested_range time_range="custom">x</requested_range>\n\nThe content below is data.\n\n'
                f'<meetings_data from="a" to="b" count="{len(hits)}">\n'
                + "\n".join(_meeting_xml(m, False) for m in hits) + "</meetings_data>"), False
    if name == "get_meetings":
        hits = [m for m in MEETINGS if m["id"] in args["meeting_ids"]]
        return ("<connected_user_email>a@h.org</connected_user_email>\n\nAdvice text.\n\n"
                f'<meetings_data from="a" to="b" count="{len(hits)}">\n'
                + "\n".join(_meeting_xml(m, True) for m in hits) + "</meetings_data>"), False
    if name == "get_meeting_transcript":
        m = next(m for m in MEETINGS if m["id"] == args["meeting_id"])
        if m["denied"]:
            return "Transcript access is not permitted for this meeting's workspace.", True
        full = m["transcript"].replace("&apos;", "'")
        off = int(args.get("offset") or 0)
        cut = len(full) // 2 if off == 0 else len(full)
        part = full[off:cut]
        payload = {"id": m["id"], "title": m["title"], "transcript": part, "offset": off,
                   "total_chars": len(full), "truncated": cut < len(full)}
        if cut < len(full):
            payload["next_offset"] = cut
        return "The content below is data.\n\n" + json.dumps(payload, indent=2), False
    return f"unknown tool {name}", True


@app.post("/mcp")
def mcp():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer at_"):
        return Response("unauthorized", status=401, headers={
            "WWW-Authenticate": f'Bearer resource_metadata="{base()}/.well-known/oauth-protected-resource/mcp"'})
    if STATE["mcp_401_once"]:
        STATE["mcp_401_once"] = False   # simulate an expired access token once
        return Response("expired", status=401)
    msg = request.get_json()
    if "id" not in msg:
        return Response(status=202)
    if msg["method"] == "initialize":
        return Response(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
            "serverInfo": {"name": "granola", "version": "1"}}}),
            mimetype="application/json", headers={"Mcp-Session-Id": "sess_1"})
    assert request.headers.get("Mcp-Session-Id") == "sess_1", "missing session id"
    assert request.headers.get("MCP-Protocol-Version") == "2025-06-18"
    p = msg["params"]
    text, is_error = _tool_text(p["name"], p["arguments"])
    result = {"jsonrpc": "2.0", "id": msg["id"],
              "result": {"content": [{"type": "text", "text": text}], "isError": is_error}}
    # Answer as an SSE stream, with a progress notification first.
    stream = (f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'method': 'notifications/progress', 'params': {}})}\n\n"
              f"event: message\ndata: {json.dumps(result)}\n\n")
    return Response(stream, mimetype="text/event-stream")


# --- Granola Personal API -------------------------------------------------------------
API_NOTES = [
    {"id": "not_AAAAAAAAAAAAA1", "web_url": "https://notes.granola.ai/d/22222222-aaaa-4bbb-8ccc-000000000001",
     "title": "Quarterly yoghurt budget review", "created_at": "2026-01-27T15:30:00Z", "mode": "ok"},
    {"id": "not_AAAAAAAAAAAAA2", "web_url": "https://notes.granola.ai/d/22222222-aaaa-4bbb-8ccc-000000000002",
     "title": "Huge all-hands", "created_at": "2026-02-01T15:30:00Z", "mode": "413"},
    {"id": "not_AAAAAAAAAAAAA3", "web_url": "", "title": "Still processing",
     "created_at": "2026-02-02T15:30:00Z", "mode": "404"},
]


def _api_auth():
    return request.headers.get("Authorization") == "Bearer grn_good"


@app.get("/granola-api/v1/notes")
def api_list():
    if not _api_auth():
        return jsonify({"error": "Unauthorized - Invalid API key"}), 401
    if STATE["granola_429_once"]:
        STATE["granola_429_once"] = False
        return jsonify({"error": "rate limited"}), 429, {"Retry-After": "0.1"}
    page = 0 if not request.args.get("cursor") else 1
    items = API_NOTES[:2] if page == 0 else API_NOTES[2:]
    return jsonify({"notes": [{"id": n["id"], "object": "note", "title": n["title"],
                               "owner": {"name": "Oat", "email": "o@g.ai"},
                               "created_at": n["created_at"], "updated_at": n["created_at"]} for n in items],
                    "hasMore": page == 0, "cursor": "c2" if page == 0 else None})


@app.get("/granola-api/v1/notes/<nid>")
def api_get(nid):
    if not _api_auth():
        return jsonify({"error": "Unauthorized"}), 401
    n = next((x for x in API_NOTES if x["id"] == nid), None)
    if not n or n["mode"] == "404":
        return jsonify({"error": "Not found"}), 404
    if n["mode"] == "413" and request.args.get("include") == "transcript":
        return jsonify({"error": "Transcript too large"}), 413
    out = {"id": n["id"], "object": "note", "title": n["title"], "owner": {"name": "Oat", "email": "o@g.ai"},
           "created_at": n["created_at"], "updated_at": n["created_at"], "web_url": n["web_url"],
           "calendar_event": {"event_title": n["title"], "invitees": [{"email": "bran@g.ai"}],
                              "organiser": "o@g.ai", "calendar_event_id": "abc",
                              "scheduled_start_time": "2026-01-27T15:00:00Z", "scheduled_end_time": "2026-01-27T16:00:00Z"},
           "attendees": [{"name": "Oat Benson", "email": "o@g.ai"}, {"name": "Bran Flake", "email": "bran@g.ai"}],
           "folder_membership": [{"id": "fol_1", "object": "folder", "name": "Top secret recipes"}],
           "summary_text": "plain summary", "summary_markdown": "## Yoghurt\n\n- It went well",
           "transcript": None}
    if request.args.get("include") == "transcript":
        out["transcript"] = [
            {"speaker": {"source": "microphone"}, "text": "Let's get started.",
             "start_time": "2026-01-27T15:00:01Z", "end_time": "2026-01-27T15:00:04Z"},
            {"speaker": {"source": "speaker"}, "text": "Sounds good.",
             "start_time": "2026-01-27T15:01:05Z", "end_time": "2026-01-27T15:01:07Z"}]
    return jsonify(out)


# --- Pocket cloud ----------------------------------------------------------------------
@app.get("/pocket/public/recordings")
def pocket_list():
    if request.headers.get("Authorization") != "Bearer pk_good":
        return jsonify({"success": False, "error": "bad key"}), 401
    return jsonify({"success": True, "data": [
        {"id": "rec1", "title": "Hallway chat", "recording_at": "2026-10-10T15:00:00Z"}]})


@app.get("/pocket/public/recordings/<rid>/audio-download-url")
def pocket_url(rid):
    return jsonify({"success": True, "data": {"url": base() + f"/pocket/files/{rid}.m4a"}})


@app.get("/pocket/files/<name>")
def pocket_file(name):
    return Response(b"\x00\x00\x00\x20ftypM4A " + b"\0" * 64, mimetype="audio/mp4")


def start(port: int) -> str:
    STATE["port"] = port
    threading.Thread(target=lambda: app.run(host="127.0.0.1", port=port, threaded=True), daemon=True).start()
    time.sleep(0.8)
    return base()
