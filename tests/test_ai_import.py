"""Tests for AI providers, Ask, Granola import (MCP + Personal API) and Pocket cloud."""
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests  # the real one, before fakes stub anything

sys.path.insert(0, str(Path(__file__).parent))
import fakes  # noqa: E402
from fakes import write_wav  # noqa: E402
import fake_cloud  # noqa: E402
from fake_cloud import CALLS, STATE  # noqa: E402

TMP = fakes.make_app_root("minutara-ai-")
BASE = fake_cloud.start(8811)
# A made-up list_meetings reply in Granola's real format (no real meetings).
SAMPLE_LIST = """<meetings_data from="Oct 1, 2026" to="Oct 17, 2026" count="3">
  <meeting id="11111111-aaaa-4bbb-8ccc-000000000001" title="Q&apos;s &amp; A&apos;s with the design team" date="Oct 16, 2026 11:31 AM EDT" meeting_at="2026-10-16T11:31:03.050-04:00" created_at="2026-10-16T11:31:03.050-04:00" updated_at="2026-10-17T01:14:53.889-04:00" url="https://notes.granola.ai/d/11111111-aaaa-4bbb-8ccc-000000000001">
    <known_participants>
    Alex Rivera (note creator)
    Sam Patel </known_participants>
    <folders />
  </meeting>
  <meeting id="11111111-aaaa-4bbb-8ccc-000000000002" title="Customer call: Northwind" date="Oct 15, 2026 9:00 AM EDT" meeting_at="2026-10-15T09:00:00.000-04:00" created_at="2026-10-15T09:00:00.000-04:00" updated_at="2026-10-15T10:00:00.000-04:00" url="https://notes.granola.ai/d/11111111-aaaa-4bbb-8ccc-000000000002">
    <known_participants>
    Alex Rivera (note creator) </known_participants>
    <folders>
      <folder id="f1" title="Clients &amp; partners" />
    </folders>
  </meeting>
  <meeting id="11111111-aaaa-4bbb-8ccc-000000000003" title="Weekly sync" date="Oct 14, 2026 10:00 AM EDT" meeting_at="2026-10-14T10:00:00.000-04:00" created_at="2026-10-14T10:00:00.000-04:00" updated_at="2026-10-14T11:00:00.000-04:00" url="https://notes.granola.ai/d/11111111-aaaa-4bbb-8ccc-000000000003">
    <known_participants>
    Alex Rivera (note creator) </known_participants>
    <folders />
  </meeting>
</meetings_data>"""

failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def raises(fn, needle):
    try:
        fn()
    except Exception as e:
        return needle.lower() in str(e).lower(), str(e)
    return False, "no error"


def wait_for(pred, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def last(path_part):
    return [c for c in CALLS if path_part in c["path"]][-1]


from minutara import ai, granola, notes  # noqa: E402
from minutara import config as config_mod  # noqa: E402

cfg = config_mod.load()

# ---- 1. Granola parsers against Granola's format -------------------------------
real = granola.parse_meetings(SAMPLE_LIST)
check("granola: parses every meeting in the list", len(real) == 3, str(len(real)))
check("granola: every meeting has id, title, time",
      all(m["id"] and m["title"] and m["meeting_at"] for m in real))
check("granola: folder element parsed", [m["folders"] for m in real][1] == ["Clients & partners"])
check("granola: participants parsed", real[0]["attendees"][:1] == ["Alex Rivera (note creator)"], str(real[0]["attendees"]))
check("granola: entities unescaped in titles", not any("&apos;" in m["title"] or "&amp;" in m["title"] for m in real))
sample = "System audio (Alex Rivera): Most expensive.\n\nSystem audio (Alex Rivera): Oh.\n\nMicrophone: You have it\nset to birds?\n\nSystem audio: Someone"
check("granola: transcript speakers mapped",
      granola.mcp_transcript_lines(sample, "Me", "Them") ==
      ["Alex Rivera: Most expensive.", "Alex Rivera: Oh.", "Me: You have it set to birds?", "Them: Someone"],
      str(granola.mcp_transcript_lines(sample, "Me", "Them")))
check("granola: note id prefers web_url uuid",
      granola.note_id_for("not_X", "https://notes.granola.ai/d/22222222-aaaa-4bbb-8ccc-000000000001")
      == "granola-22222222-aaaa-4bbb-8ccc-000000000001")

# ---- 2. AI providers -----------------------------------------------------------
P = cfg.ai["providers"]
P["anthropic"].update(api_key="sk-ant-good", base_url=BASE + "/anthropic")
P["openai"].update(api_key="sk-good", base_url=BASE + "/openai/v1")
P["gemini"].update(api_key="g-good", base_url=BASE + "/gemini/v1beta")
P["ollama"].update(base_url=BASE + "/ollama")
P["compatible"].update(base_url=BASE + "/openai/v1", model="local-model")
msgs = [{"role": "user", "content": "hello"}]

cfg.ai["provider"] = "anthropic"
out = ai.complete(cfg, "Write meeting notes", msgs)
call = last("/anthropic/v1/messages")
check("claude: answer parsed from stream", out.startswith("# Budget"), out[:40])
check("claude: x-api-key + anthropic-version headers",
      call["headers"].get("x-api-key") == "sk-ant-good" and call["headers"].get("anthropic-version"))
check("claude: default model is claude-opus-5-5", call["json"]["model"] == "claude-opus-5-5")
check("claude: fallback beta + fallbacks=default",
      "server-side-fallback-2026-07-01" in call["headers"].get("anthropic-beta", "")
      and call["json"].get("fallbacks") == "default", str(call["headers"].get("anthropic-beta")))
check("claude: effort sent via output_config", call["json"].get("output_config") == {"effort": "medium"})
check("claude: streams", call["json"].get("stream") is True)
check("claude: system prompt sent", call["json"].get("system") == "Write meeting notes")
P["anthropic"].update(model="claude-haiku-5-5", effort="")
ai.complete(cfg, "x", msgs)
call = last("/anthropic/v1/messages")
check("claude: no fallbacks for models without it", "fallbacks" not in call["json"]
      and "server-side-fallback" not in call["headers"].get("anthropic-beta", ""))
check("claude: blank effort omitted", "output_config" not in call["json"])
P["anthropic"].update(model="claude-opus-5-5", effort="medium")
STATE["anthropic_refuse"] = True
ok, msg = raises(lambda: ai.complete(cfg, "x", msgs), "declined")
check("claude: refusal surfaces clearly", ok, msg)
STATE["anthropic_refuse"] = False
P["anthropic"]["api_key"] = "bad"
ok, msg = raises(lambda: ai.complete(cfg, "x", msgs), "rejected the API key")
check("claude: bad key message", ok, msg)
P["anthropic"]["api_key"] = "sk-ant-good"
P["anthropic"]["model"] = "claude-nope"
ok, msg = raises(lambda: ai.complete(cfg, "x", msgs), "doesn't recognise")
check("claude: unknown model message", ok, msg)
P["anthropic"]["model"] = "claude-opus-5-5"
check("claude: model list", ai.list_models(cfg, "anthropic") == ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"])

cfg.ai["provider"] = "openai"
out = ai.complete(cfg, "Write meeting notes", msgs)
call = last("/openai/v1/responses")
check("openai: Responses API by default", out.startswith("# Budget") and call["json"]["instructions"] == "Write meeting notes"
      and call["json"]["input"] == msgs and call["json"]["max_output_tokens"] == 16000)
check("openai: bearer key", call["headers"]["authorization"] == "Bearer sk-good")
P["openai"]["api_style"] = "chat_completions"
ai.complete(cfg, "Write meeting notes", msgs)
call = last("/openai/v1/chat/completions")
check("openai: chat style + max_completion_tokens",
      call["json"]["messages"][0] == {"role": "system", "content": "Write meeting notes"}
      and call["json"].get("max_completion_tokens") == 16000)
P["openai"]["api_key"] = "nope"
ok, msg = raises(lambda: ai.complete(cfg, "x", msgs), "rejected the API key")
check("openai: bad key message", ok, msg)
P["openai"]["api_key"] = "sk-good"
check("openai: model list", ai.list_models(cfg, "openai") == ["gpt-6-luna", "gpt-6.1-sol"])

cfg.ai["provider"] = "compatible"
ai.complete(cfg, "x", msgs)
call = last("/openai/v1/chat/completions")
check("compatible: chat + max_tokens + no key needed",
      call["json"]["model"] == "local-model" and call["json"].get("max_tokens") == 4096
      and call["headers"]["authorization"] == "Bearer not-needed")

cfg.ai["provider"] = "gemini"
out = ai.complete(cfg, "Write meeting notes", msgs)
call = last("/gemini/")
check("gemini: key header, model in path, system instruction",
      call["headers"].get("x-goog-api-key") == "g-good"
      and call["path"].endswith("/models/gemini-flash-latest:generateContent")
      and call["json"]["systemInstruction"]["parts"][0]["text"] == "Write meeting notes")
check("gemini: thought parts dropped", out.startswith("# Budget"), out[:30])
check("gemini: model list keeps generateContent models", ai.list_models(cfg, "gemini") == ["gemini-flash-latest"])
P["gemini"]["api_key"] = "bad"
ok, msg = raises(lambda: ai.complete(cfg, "x", msgs), "gemini")
check("gemini: error surfaced", ok, msg)
P["gemini"]["api_key"] = "g-good"

cfg.ai["provider"] = "ollama"
out = ai.complete(cfg, "Write meeting notes", msgs)
call = last("/ollama/api/chat")
check("ollama: num_ctx + no streaming", call["json"]["options"]["num_ctx"] == 16384 and call["json"]["stream"] is False)
check("ollama: <think> stripped", out.startswith("# Budget"), out[:30])
check("ollama: model list", ai.list_models(cfg, "ollama") == ["llama3.2:3b", "qwen3:4b"])
P["ollama"]["model"] = "missing:1b"
ok, msg = raises(lambda: ai.complete(cfg, "x", msgs), "ollama pull missing:1b")
check("ollama: missing model tells you the pull command", ok, msg)
P["ollama"]["model"] = "llama3.2:3b"
good_base = P["ollama"]["base_url"]
P["ollama"]["base_url"] = "http://127.0.0.1:9"
ok, msg = raises(lambda: ai.complete(cfg, "x", msgs), "isn't running")
check("ollama: not running message", ok, msg)
P["ollama"]["base_url"] = good_base

# chunked summary for a meeting longer than the provider's input budget
P["ollama"]["max_input_chars"] = 6000
n_before = len([c for c in CALLS if c["path"] == "/ollama/api/chat"])
long_t = "\n".join(f"[{i // 60:02d}:{i % 60:02d}] Them: sentence number {i} about the budget." for i in range(900))
text = ai.summarize(cfg, "Long one", "2026-10-01 10:00", long_t, "my rough note")
chat_calls = [c for c in CALLS if c["path"] == "/ollama/api/chat"][n_before:]
check("summary: long meeting split into parts then combined",
      len(chat_calls) >= 4 and "condensing one part" in chat_calls[0]["json"]["messages"][0]["content"]
      and "meeting notes" in chat_calls[-1]["json"]["messages"][0]["content"], str(len(chat_calls)))
check("summary: every request within budget",
      all(sum(len(m["content"]) for m in c["json"]["messages"]) <= 6000 + 3000 for c in chat_calls))
check("summary: timestamps stripped before sending", "[00:01]" not in chat_calls[0]["json"]["messages"][-1]["content"])
P["ollama"]["max_input_chars"] = 40000
cfg.ai["provider"] = "off"
ok, msg = raises(lambda: ai.complete(cfg, "x", msgs), "turned off")
check("ai: off refuses to send anything", ok, msg)

# ---- 3. server: settings, AI notes, Ask ------------------------------------------
config_mod.save(cfg)
import minutara.server as srv  # noqa: E402

app, rec = srv.create_app(cfg)
c = app.test_client()
T = cfg.server_token
U = lambda p: p + ("&" if "?" in p else "?") + "token=" + T  # noqa: E731

conf = c.get(U("/api/config")).get_json()
check("config: AI keys never sent to the browser",
      "api_key" not in json.dumps(conf["ai"]) .replace("api_key_set", "")
      and conf["ai"]["providers"]["anthropic"]["api_key_set"] is True)
c.post(U("/api/config"), json={"ai": {"provider": "anthropic", "providers": {"anthropic": {"api_key": "  "}}}})
check("config: blank key keeps the saved one", cfg.ai["providers"]["anthropic"]["api_key"] == "sk-ant-good")
c.post(U("/api/config"), json={"ai": {"providers": {"openai": {"api_key": None}, "evil": {"x": 1}}}})
check("config: null clears a key, unknown providers ignored",
      cfg.ai["providers"]["openai"]["api_key"] == "" and "evil" not in cfg.ai["providers"])
c.post(U("/api/config"), json={"ai": {"provider": "skynet"}})
check("config: unknown provider falls back to off", cfg.ai["provider"] == "off")
c.post(U("/api/config"), json={"ai": {"provider": "anthropic", "providers": {"anthropic": {"max_tokens": "8000"}}}})
saved = json.loads((TMP / "config.json").read_text())
check("config: nested settings persisted with types",
      saved["ai"]["provider"] == "anthropic" and saved["ai"]["providers"]["anthropic"]["max_tokens"] == 8000)
cfg.ai["providers"]["openai"]["api_key"] = "sk-good"
r = c.post(U("/api/ai/models"), json={"provider": "openai"}).get_json()
check("api: fetch models", r["ok"] and "gpt-6.1-sol" in r["models"])
r = c.post(U("/api/ai/test"), json={"provider": "anthropic"}).get_json()
check("api: connection test", r["ok"] and "OK" in r["message"], str(r))
d = c.get(U("/api/ai/defaults")).get_json()
check("api: built-in prompts exposed for the settings page", "chief of staff" in d["summary_prompt"])

# a note with a transcript → AI notes on demand
notes.create(cfg, "20261015-1000-budget", title="Budget review", created_at="2026-10-15T10:00:00-04:00")
(cfg.transcripts_dir / "20261015-1000-budget.txt").write_text("[00:01] Me: Budget is down.\n[00:05] Them: Events cut.")
r = c.post(U("/api/notes/20261015-1000-budget/summarize"))
check("summarize: queued", r.status_code == 200, r.get_data(as_text=True))
check("summarize: AI notes saved on the note",
      wait_for(lambda: (notes.load(cfg, "20261015-1000-budget") or {}).get("summary", "").startswith("# Budget")))
g = c.get(U("/api/notes/20261015-1000-budget")).get_json()
check("note view: summary + meta", g["has_summary"] and g["summary_meta"]["provider"] == "anthropic"
      and g["summary_meta"]["model"] == "claude-opus-5-5")
user_msg = last("/anthropic/v1/messages")["json"]["messages"][-1]["content"]
check("summarize: sends title, transcript (no timestamps) as data",
      "Budget review" in user_msg and "<transcript>" in user_msg and "Me: Budget is down." in user_msg
      and "[00:01]" not in user_msg)

# failure is recorded on the note
cfg.ai["providers"]["anthropic"]["api_key"] = "bad"
c.post(U("/api/notes/20261015-1000-budget/summarize"))
check("summarize: failure recorded, old notes kept",
      wait_for(lambda: "rejected" in (notes.load(cfg, "20261015-1000-budget") or {}).get("summary_error", ""))
      and notes.load(cfg, "20261015-1000-budget")["summary"].startswith("# Budget"))
cfg.ai["providers"]["anthropic"]["api_key"] = "sk-ant-good"

# auto: transcription finishing triggers AI notes
write_wav(cfg.recordings_dir / "20261016-0900-standup.wav")
notes.sync(cfg)
c.post(U("/api/notes/20261016-0900-standup/transcribe"))
check("auto: transcript then AI notes, unattended",
      wait_for(lambda: (notes.load(cfg, "20261016-0900-standup") or {}).get("summary", "").startswith("# Budget"), 30))

# Ask
r = c.post(U("/api/ask"), json={"question": "What was cut?", "note_id": "20261015-1000-budget"}).get_json()
check("ask: about one meeting", r["ok"] and r["answer"].startswith("ANSWER") and r["sources"][0]["title"] == "Budget review")
ctx = last("/anthropic/v1/messages")["json"]["messages"][-1]["content"]
check("ask: meeting material sent as data, with the question", "<meeting " in ctx and "Question: What was cut?" in ctx)
r = c.post(U("/api/ask"), json={"question": "budget decisions?", "history": [
    {"role": "user", "content": "earlier q"}, {"role": "assistant", "content": "earlier a"}]}).get_json()
call = last("/anthropic/v1/messages")["json"]
check("ask: across meetings with sources + history",
      r["ok"] and any(s["title"] == "Budget review" for s in r["sources"])
      and call["messages"][0] == {"role": "user", "content": "earlier q"})
cfg.ai["provider"] = "off"
r = c.post(U("/api/ask"), json={"question": "x"})
check("ask: refuses when AI is off", r.status_code == 400)
cfg.ai["provider"] = "anthropic"

# ---- 4. Granola import via MCP (sign in, then import) ------------------------------
c.post(U("/api/config"), json={"granola": {"method": "mcp", "mcp_url": BASE + "/mcp", "since": "2025-01-01"}})
r = c.post(U("/api/granola/import"))
check("granola: import needs sign-in first", r.status_code == 400)
r = c.post(U("/api/granola/signin")).get_json()
check("granola: sign-in URL from discovered auth server", r["ok"] and r["url"].startswith(BASE + "/auth/authorize"), str(r))
reg = list(STATE["clients"].values())[-1]
check("granola: Minutara registered itself (DCR) with a local redirect",
      reg["redirect_uris"] == [f"http://127.0.0.1:{cfg.server_port}/oauth/granola/callback"]
      and reg["token_endpoint_auth_method"] == "none")
browser = requests.get(r["url"], allow_redirects=False)   # Granola's page approves
loc = urlparse(browser.headers["Location"])
check("granola: Granola redirects back to Minutara", loc.path == "/oauth/granola/callback")
page = c.get(f"{loc.path}?{loc.query}")
check("granola: callback exchanges the code (PKCE verified)", page.status_code == 200 and b"Signed in" in page.data,
      page.get_data(as_text=True)[:200])
check("granola: tokens stored locally", granola.load_auth().get("access_token") == "at_1")
check("granola: import starts by itself after sign-in",
      wait_for(lambda: c.get(U("/api/granola/status")).get_json()["state"] == "done", 30),
      str(c.get(U("/api/granola/status")).get_json()))
st = c.get(U("/api/granola/status")).get_json()
check("granola: counts", st["imported"] == 3 and st["failed"] == 0, str({k: st[k] for k in ("imported", "failed", "skipped")}))
windows = [c_["json"]["params"]["arguments"] for c_ in CALLS
           if c_["path"] == "/mcp" and c_["json"] and c_["json"].get("params", {}).get("name") == "list_meetings"]
check("granola: history listed in date windows", len(windows) >= 5 and windows[0]["custom_start"] == "2025-01-01")
n1 = notes.load(cfg, "granola-11111111-aaaa-4bbb-8ccc-000000000001")
n2 = notes.load(cfg, "granola-11111111-aaaa-4bbb-8ccc-000000000002")
n3 = notes.load(cfg, "granola-11111111-aaaa-4bbb-8ccc-000000000003")
check("granola: title unescaped, source, date kept",
      n1["title"] == "Board prep: Q4 budget & hiring" and n1["source"] == "granola"
      and datetime.fromisoformat(n1["created_at"]) == datetime.fromisoformat("2025-12-03T10:02:00-05:00"))
check("granola: Granola's AI notes imported", "Cut events by 8%" in n1["summary"]
      and n1["summary_meta"]["provider"] == "granola")
t1 = (cfg.transcripts_dir / f"{n1['id']}.txt").read_text()
check("granola: transcript joined across pages with speakers",
      t1.splitlines() == ["Me: Let's start.", "Alex Rivera: Budget is down 8%.", "Them: Somebody else."], t1)
check("granola: folder + your private notes", n2["folder"] == "Client calls" and n2["notes"].startswith("- ask about SSO")
      and any(f["name"] == "Client calls" for f in notes.folders(cfg)))
check("granola: denied transcript recorded, meeting still imported",
      n3 and "not permitted" in n3["granola"]["transcript_error"]
      and not (cfg.transcripts_dir / f"{n3['id']}.txt").exists()
      and any("without a transcript" in e for e in st["errors"]))
arch = json.loads((cfg.archive_dir / f"{n1['id']}.json").read_text())
check("granola: raw archive kept", arch["raw"]["details_block"].startswith("<meeting") and arch["raw"]["transcript_text"])
check("granola: imported notes are not re-summarised automatically", n1["summary_meta"]["provider"] == "granola")
c.post(U("/api/granola/import"))
wait_for(lambda: c.get(U("/api/granola/status")).get_json()["state"] == "done", 20)
st = c.get(U("/api/granola/status")).get_json()
check("granola: running again only adds what's new", st["imported"] == 0 and st["skipped"] == 3, str(st))
notes.update(cfg, n2["id"], notes="typed in Minutara")
STATE["mcp_401_once"] = True
c.post(U("/api/granola/import"), json={"refresh": True})
wait_for(lambda: c.get(U("/api/granola/status")).get_json()["state"] == "done", 20)
st = c.get(U("/api/granola/status")).get_json()
check("granola: expired token refreshed transparently", STATE["refreshes"] >= 1 and st["imported"] == 3, str(st))
check("granola: re-import keeps notes typed in Minutara", notes.load(cfg, n2["id"])["notes"] == "typed in Minutara")
c.post(U("/api/granola/signout"))
check("granola: sign out forgets tokens", not granola.load_auth())

# ---- 5. Granola import via the Personal API ------------------------------------------------
c.post(U("/api/config"), json={"granola": {"method": "api", "api_base": BASE + "/granola-api/v1",
                                            "api_key": "grn_bad", "since": "2026-01-01"}})
c.post(U("/api/granola/import"))
wait_for(lambda: c.get(U("/api/granola/status")).get_json()["state"] in ("needs_auth", "error", "done"))
check("api: bad key asks for a new one", c.get(U("/api/granola/status")).get_json()["state"] == "needs_auth")
c.post(U("/api/config"), json={"granola": {"api_key": "grn_good"}})
check("api: key saved but never echoed", "grn_good" not in json.dumps(c.get(U("/api/config")).get_json()))
c.post(U("/api/granola/import"))
wait_for(lambda: c.get(U("/api/granola/status")).get_json()["state"] == "done", 30)
st = c.get(U("/api/granola/status")).get_json()
check("api: imported 2, the unfinished one reported", st["imported"] == 2 and st["failed"] == 1
      and any("hasn't finished" in e for e in st["errors"]), str(st))
a1 = notes.load(cfg, "granola-22222222-aaaa-4bbb-8ccc-000000000001")
check("api: fields mapped", a1 and a1["title"] == "Quarterly yoghurt budget review"
      and a1["folder"] == "Top secret recipes" and a1["attendees"] == ["Oat Benson", "Bran Flake"]
      and a1["summary"].startswith("## Yoghurt"))
ta = (cfg.transcripts_dir / f"{a1['id']}.txt").read_text().splitlines()
check("api: transcript with timestamps and sides", ta == ["[00:00] Me: Let's get started.", "[01:04] Them: Sounds good."], str(ta))
a2 = notes.load(cfg, "granola-22222222-aaaa-4bbb-8ccc-000000000002")
check("api: oversized transcript → note kept, reason recorded", a2 and "too large" in a2["granola"]["transcript_error"])
list_calls = [c_ for c_ in CALLS if c_["path"] == "/granola-api/v1/notes"]
check("api: paginates with cursor, filters by date, survives 429",
      any(c_["args"].get("cursor") == "c2" for c_ in list_calls)
      and all(c_["args"].get("created_after") == "2026-01-01" for c_ in list_calls) and len(list_calls) >= 3)

# ---- 6. Pocket ------------------------------------------------------------------------------
r = c.get(U("/api/pocket/status")).get_json()
check("pocket: status (no drive here)", r["usb"] == "" and r["cloud_enabled"] is False)
check("pocket: cloud import needs the toggle", c.post(U("/api/pocket/cloud-import"), json={"days": 30}).status_code == 400)
c.post(U("/api/config"), json={"use_pocket_api": True, "pocket_api_key": "pk_good",
                                "pocket_base_url": BASE + "/pocket", "usb_volume_label": "MYPOCKET"})
check("pocket: drive name + address editable", cfg.usb_volume_label == "MYPOCKET" and cfg.pocket_base_url == BASE + "/pocket")
c.post(U("/api/pocket/cloud-import"), json={"days": 30})
check("pocket: cloud recordings imported as notes",
      wait_for(lambda: c.get(U("/api/pocket/status")).get_json()["cloud_job"]["state"] == "done")
      and notes.load(cfg, "pocket-cloud-rec1") and (cfg.recordings_dir / "pocket-cloud-rec1.m4a").exists(),
      str(c.get(U("/api/pocket/status")).get_json()))
check("pocket: imported recording gets transcribed",
      wait_for(lambda: (cfg.transcripts_dir / "pocket-cloud-rec1.txt").exists(), 20))

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("ALL PASS")
