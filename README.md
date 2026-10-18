# Scribe

Local-first meeting capture and transcription for Windows. A Granola
replacement that runs on your own machine, treats a [Pocket](https://heypocket.com)
device as a second input, and sends nothing to anyone's servers unless you
switch it on.

```
  Zoom/Teams on your PC ──► WASAPI loopback + mic ──┐
                                                    ├─► local Whisper ─► transcripts/*.txt
  Pocket device ──► USB-C (plain drive) ────────────┘
                                                    ⋯► Pocket cloud (off by default)

  Phone ──► http://<laptop>:8765 ──► start / stop / read
```

## Why it exists

Meeting note-takers either charge a subscription (Granola, $14/mo), put a bot
in your call, or route your conversations through their cloud. Scribe does
none of that:

- **Capture is local.** Windows WASAPI loopback records what your speakers
  play; your mic is recorded separately. No bot joins, nobody is notified,
  nothing leaves the machine.
- **Transcription is local.** [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
  runs on your CPU (or GPU). The model lives in a folder next to the app.
- **Speaker attribution is free.** Mic and loopback are kept as separate
  channels of one stereo WAV — left is you, right is everyone else — and each
  channel is transcribed independently. The you-vs-them split comes from the
  wiring, not from a diarization model guessing.
- **The Pocket is treated as a drive.** With USB Mode on, the device mounts
  as a `POCKET` volume with a `RECORD` folder. Scribe copies new files off it
  and transcribes them locally. In-person meetings and Zoom calls end up in
  the same `transcripts/` folder.

Transcripts are plain `.txt` — paste them into whatever LLM you like, or
point your notes system at the folder.

## Download the app (easiest)

Grab **Scribe-windows.zip** from the [latest release](https://github.com/nexusforce/scribe/releases/latest),
extract it somewhere permanent (or onto a USB stick) and double-click
**Scribe.exe**. No Python needed. It's built automatically on every merge
to `main`, and each build is tested on Windows before it's published.

- The first time, Windows says "Windows protected your PC" (the app isn't
  code-signed): click **More info → Run anyway**. Unblocking the zip first
  (right-click → Properties → Unblock) avoids it.
- Your settings, notes and recordings live next to Scribe.exe. To update,
  extract a newer zip over the folder; your data stays.
- `scribe-cli.exe` in the same folder is the command-line version
  (`scribe-cli doctor` checks that every part Scribe needs works).
- It's an x64 app; ARM laptops run it through Windows' built-in emulation.
- Scribe unblocks its own window files if Windows marked them as
  downloaded; if it ever opens in an Edge window instead (no floating
  recorder), Settings → Meetings says why, and so does `scribe-cli doctor`.
- Scribe opens in its own window, drawn by WebView2 (the web engine built
  into Windows 10/11), so the taskbar button and a pinned icon are Scribe's.
  It isn't a website: the window talks only to Scribe running on your PC.
  Closing the window quits Scribe; a recording in progress is stopped and
  saved first, and a transcription under way finishes in the background.

To build the .exe yourself: `powershell -ExecutionPolicy Bypass -File build\build_exe.ps1`.

## Quick start (from a clone)

```powershell
git clone https://github.com/nexusforce/scribe
cd scribe
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy config.example.json config.json
```

Then **double-click `Scribe App.bat`** — the app opens in its own window.
The engine runs windowless in the background and logs to `scribe.log`; quit
it from Settings → Quit. If you want a visible console instead, run
`scribe.bat serve --open`.

The same things are available as commands, if you prefer a terminal:

```powershell
scribe.bat record --title "Board sync"    # capture a Zoom/Teams call, Ctrl+C to stop
scribe.bat from-usb                       # pull new recordings off the Pocket
scribe.bat serve                          # the GUI server (add --open to pop the window)
scribe.bat config                         # show toggles and whether the drive is connected
```

First transcription downloads the Whisper model (~500 MB for `small.en`).

On a **Snapdragon / ARM64** laptop, install the **x64** build of Python from
python.org (the "Windows installer (64-bit)"), *not* the ARM64 build — the
capture and transcription wheels are x64-only and Windows 11 runs them under
emulation. See [Windows on ARM](#windows-on-arm-snapdragon-laptops) below.

## Portable build (run from a USB stick)

```powershell
powershell -ExecutionPolicy Bypass -File build\make_portable.ps1 -Target E:\Scribe
```

Run once on a machine with internet; the stick works offline afterward. It
bundles embeddable Python, all dependencies, the app, and a pre-baked Whisper
model (~2 GB total). Nothing is installed on the host, no admin needed, and
all data — recordings, transcripts, config — stays on the stick.

Realities of stick life: use USB 3.0 (the model is read on every run),
SmartScreen may grumble at an unsigned interpreter on removable media, and a
locked-down work laptop with application allowlisting may refuse to run it at
all.

## Windows on ARM (Snapdragon laptops)

Use **x64 Python under emulation** — don't try a native ARM64 install. Two
load-bearing dependencies, `PyAudioWPatch` (WASAPI capture) and `ctranslate2`
(faster-whisper's engine), publish no `win_arm64` wheels, so a native ARM64
Python cannot record or transcribe. Windows 11 on ARM runs x64 Python
transparently through the Prism emulator, so both install paths work:

- **Installed clone:** grab the *x64* Python from python.org (the "Windows
  installer (64-bit)", not the ARM64 one), then follow
  [Quick start](#quick-start-from-a-clone) unchanged.
- **Portable stick:** the build script already fetches x64 embeddable Python,
  so it works as-is — even when run on the ARM machine itself:

  ```powershell
  powershell -ExecutionPolicy Bypass -File build\make_portable.ps1 -Target E:\Scribe
  ```

Notes for ARM:

- **Windows 11 is required** (its x64 emulation covers everything here);
  24H2 or later is noticeably faster because Prism adds AVX/AVX2 support,
  which faster-whisper uses heavily.
- **Transcription pays an emulation tax.** If `small.en` feels slow, build
  with `-Model base.en` (or set `"whisper_model": "base.en"` in config.json)
  and step back up if quality allows.
- Recording and the phone remote are I/O-bound and run fine under emulation.

## The app

Laid out like Granola: every meeting is a **note**. The sidebar has search
(Ctrl+K, covers titles, your notes and transcripts), Home, and your folders.
Home shows today's date and a **Start recording** card, then your meetings
grouped by day, each labelled when it's still recording, transcribing, or
not transcribed yet. **New note** (top right) starts recording straight
away; the ↓ button pulls new recordings off your Pocket.

A note has a title, chips for date, length, source and folder, and three tabs:

- **AI notes** — Granola-style "enhanced" notes: topics, decisions and next
  steps written from the transcript and your rough notes (see below).
- **My notes** — type while the meeting runs; it saves as you go.
- **Transcript** — drawn as a conversation, you on the right and them on
  the left (the stereo recording knows who said what). Click any timestamp
  to play the recording from that moment.

The pill at the bottom works like Granola's: pause, resume and stop while
recording, then a player once the audio is saved. The **…** menu downloads
or copies the whole meeting, downloads the recording, re-runs
transcription, or deletes the note.

Capture and transcription are separate steps. Audio streams to disk while
you record — a crash loses seconds, not the meeting — and the note has its
recording the moment you press Stop, whether or not it's ever transcribed.
By default transcription starts automatically (the lamp breathes blue);
turn that off in Settings and press **Transcribe** on the note when you're
ready. You can start the next recording while the last one transcribes.

**Settings** is a single preferences page: auto-transcribe, theme
(System/Light/Dark), microphone, recordings folder, speech model, speaker
names, the Pocket options and API key, plus **Link your phone**, the
activity log and Quit.

**Microphone** and **Speakers** (Settings → Recording) pick what Scribe
records: your side from the mic, their side from whichever output your
sound comes out of (speakers, headphones, Bluetooth, a monitor). Left on
default, Scribe follows Windows' default output.

**Test mic** and **Test speakers** record four seconds, play them back and
say what they found: working, too quiet, silent (often Windows' microphone
privacy switch), static, or a device whose driver sends audio at the wrong
speed. The speakers test plays its own chime, so nothing else needs to be
playing. Use them before a real meeting, and after changing devices.

**Meetings.** When Zoom, Teams or Webex starts using your microphone, a
small pop-up appears ("Zoom meeting · Take notes? Start / Not now"), like
Granola's. Scribe asks Windows which programs are recording from a
microphone, two ways, every two seconds: Windows' audio system (the active
recording sessions on every microphone, with the program behind each) and
the privacy record behind the taskbar's mic icon. Nothing is listened to. Teams, Google Meet and Zoom in a web browser count too: the
browser is using the mic and one of its windows is titled like a meeting
("… | Microsoft Teams", "Meet – …"). When the meeting app lets go of the mic
while you're recording, the pop-up asks whether to stop. Scribe has to be
open (it can be minimized). The app list and page titles are settings, and
every mic change is logged ("microphone in use by: …") and shown live in
Settings → Meetings, so a missed meeting is easy to diagnose.

**Floating recorder.** While recording, a small always-on-top window with
the timer, pause, stop and a button back to Scribe appears whenever
Scribe's own window isn't in front. Drag it anywhere (it remembers where);
the – button tucks it away for the rest of that recording. Both are on by
default and can be turned off in Settings → Meetings, which has a **Show
me** preview for each.

Playback is at a normal volume. The player gets a listening copy of each
recording: both sides mixed into both ears, each brought to a normal speech
level. The recording itself is untouched; transcription and **Download**
use the original, which keeps you and them on separate channels.

Recordings follow the clock. Windows sends nothing from the speakers while
nothing plays, and some drivers deliver audio too fast or too slow; Scribe
fills the gaps with silence and drops anything past real time, so a
10-second recording is 10 seconds long. Each recording logs what every
device actually did (`scribe.log`, lines starting `capture:`).

**Export.** Each tab has **Copy** and **Download** for that part: AI notes,
My notes or the transcript. **… → Download everything** / **Copy
everything** gives the whole meeting as one readable document: a title,
then the date, length, folder and attendees, then AI notes, your notes and
the transcript. **Settings → Export → Export all** downloads a zip with one
such document per meeting (all meetings or one folder; pick which parts;
optionally with the recordings). Files are plain text (`.txt`, opens in
Notepad, Word or an email) or Markdown (`.md`, for Notion, Obsidian and
similar), set under **File type**. Names start with the date, e.g.
`2026-10-18 1410 Weekly sync.txt`, so they sort by date.

**Meeting notes folder.** Scribe also keeps a readable copy of every
meeting in `Meeting notes\` next to Scribe.exe, updated a few seconds after
its AI notes, your notes or its transcript change, and removed when you
delete the meeting. Point it at any folder (OneDrive, a shared drive) under
**Settings → Export**, or turn it off there. **Save all now** writes every
existing meeting; **Open folder** opens it in Explorer. Scribe's own data
stays where it was: notes and AI notes in `notes\` (one JSON file per
meeting), transcripts in `transcripts\`, audio in `recordings\`.

Files that were already in `recordings/` or `transcripts/` (from the CLI,
the Pocket, or older versions of Scribe) show up as notes automatically.
Each note is a small JSON file in `notes/`; audio and transcripts stay where
they always were.

What Scribe deliberately doesn't copy from Granola: calendar sync, sharing
and billing.

## AI notes and Ask

Off until you pick a provider in **Settings → AI notes & Ask**:

| Provider | Where your transcript goes | Notes |
|---|---|---|
| Claude (Anthropic) | Anthropic | Official SDK. Default model `claude-opus-5-5`, effort `medium`, automatic refusal fallback |
| OpenAI | OpenAI | Official SDK, Responses API (Chat Completions selectable) |
| Google Gemini | Google | REST; default model `gemini-flash-latest`, which tracks Google's newest Flash |
| Ollama | **Nowhere** — runs on this computer | Install Ollama, `ollama pull llama3.2:3b` (or any model), pick it in Settings |
| OpenAI-compatible | The server you set | LM Studio, OpenRouter, vLLM, gateways… |

Each provider keeps its own key, model and limits, so switching doesn't lose
anything. **Fetch models** lists what your key can actually use and **Test**
sends a one-line message, so a renamed model shows up immediately rather than
mid-meeting. Keys can also come from `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or
`GEMINI_API_KEY`.

With a provider on, AI notes are written automatically after each transcript
(or press **Generate** on a note), and an **Ask anything** box appears at the
bottom of Home and of every note — like Granola's. On a note it answers about
that meeting (try "Write follow-up email"); on Home it reads your recent
meetings plus any that match the question, and links the ones it used.

Meetings longer than a model can read at once (small local models,
especially) are summarized in parts and then combined — never silently cut.
Both prompts are editable under **Prompts**; leave them blank to keep
Scribe's built-in ones.

Everything that talks to the outside world — addresses, model names, API
style, output-limit parameter, timeouts, input budgets, Ollama's context
window — is a setting (Settings → Advanced, or the `ai` section of
`config.json`), so a provider changing its API is a settings change, not a
code change.

## Moving from Granola

**Settings → Import from Granola** copies your history into Scribe so nothing
is lost when you cancel: titles, dates, attendees, folders, Granola's AI
notes, your own typed notes and full transcripts (Granola keeps no audio, so
there's none to copy).

- **Sign in with Granola** (default) uses Granola's official MCP server: a
  browser window opens, you approve, and the import starts by itself. Works on
  Granola's paid plans. Do this on the computer running Scribe.
- **Granola API key** uses Granola's Personal API (`grn_…` keys need a
  Business or Enterprise plan).

Pick "Ones I recorded or attended" or "Everything I can see", and a start
date. Running the import again only adds new meetings, so an interrupted
import just continues; **Re-import** refreshes everything but keeps notes
you've typed in Scribe. Meetings whose transcript Granola won't release are
still imported, with the reason listed. Granola's raw responses are also kept
in `granola_archive/`, so even fields Scribe doesn't show are preserved.

## Pocket

The ↓ button on Home (or **Settings → Pocket → Pull recordings**) copies new
recordings off the Pocket over USB-C — turn on USB Mode in the Pocket app
first. Settings shows whether the drive is connected, and lets you change the
drive name and folder if Pocket ever renames them. With **Pocket cloud** on
and your key added, **Import from Pocket's cloud** downloads recordings from
your Pocket account too; the API address is a setting as well.

## The phone remote

Settings → **Link your phone** (or the phone icon in the sidebar) shows a QR
code. Scan it on the same Wi-Fi and add the page to your home screen: it's
the same app, laid out for a phone. Double-clicking the launcher while
Scribe is already running doesn't start a second copy — it just reopens the
window.

- Phone and laptop must share a network. Corporate/guest Wi-Fi with client
  isolation blocks this; a phone hotspot is the fallback.
- Auth is a token in the URL, generated on first run into `config.json`.
  Plain HTTP — treat it as LAN-only and never port-forward it.
- Allow the Windows Firewall prompt on **Private** networks only.

## Configuration

Copy `config.example.json` to `config.json`. Everything cloud-facing defaults
to **off**:

| Setting | On means |
|---|---|
| `use_pocket_api` | Master switch for anything network-facing |
| `push_to_pocket` | Upload PC-captured audio into your Pocket account |
| `prefer_pocket_transcripts` | Use Pocket's transcription instead of local Whisper |
| `delete_from_device` | Remove files from the device after a verified copy |

`config.json` is gitignored because it holds your API keys (Pocket, AI
providers, Granola) and the remote's auth token. Don't commit it. Your
Granola sign-in lives in `granola_auth.json`, also gitignored.

For the Pocket API, use a **personal** key (`pk_...` from Settings →
Developer → API Keys) — organization keys are rejected by the upload endpoint.

## Commands

| Command | Does |
|---|---|
| `record --title "..."` | Capture system audio + mic until Ctrl+C, then transcribe |
| `from-usb [--watch]` | Copy new recordings off the Pocket drive and transcribe |
| `pull [--days N] [--id X]` | Fetch from Pocket's cloud API (requires the toggle) |
| `transcribe <file>` | Transcribe any audio file you already have |
| `serve [--port N] [--open]` | Start the app (and open its window with `--open`) |
| `config` | Show current settings and device status |
| `doctor [--transcribe]` | Check that every part Scribe needs works on this machine |

## Known unknowns

Marked `UNVERIFIED` in the source, in honesty rather than confidence:

- ~~File layout inside the Pocket's `RECORD` folder~~ — **verified** on a real
  device (2026-01): one folder per day, mp3 audio (`RECORD\2026-01-15\*.mp3`).
  Ingest handles the dated folders and keeps the date in the local filename.
- **Pocket API response shapes** — the docs render every example payload as
  `null`, so field names are inferred. Print one raw response and correct.
- Enabling USB Mode requires the Pocket phone app, and opening the app can
  trigger cloud sync of unsynced recordings. Test the loop with a throwaway
  recording before a real meeting.

## Consent

Scribe records conversations without notifying participants — that's the
point of bot-free capture, and it's also a legal question that varies by
jurisdiction. Recording-consent laws are on you, not the tool.

## License

MIT
