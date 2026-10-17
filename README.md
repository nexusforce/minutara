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

A note has a title, chips for date, length, source and folder, and two tabs:

- **My notes** — type while the meeting runs; it saves as you go.
- **Transcript** — drawn as a conversation, you on the right and them on
  the left (the stereo recording knows who said what). Click any timestamp
  to play the recording from that moment.

The pill at the bottom works like Granola's: pause, resume and stop while
recording, then a player once the audio is saved. The **…** menu downloads
the recording or transcript, re-runs transcription, or deletes the note.

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

Files that were already in `recordings/` or `transcripts/` (from the CLI,
the Pocket, or older versions of Scribe) show up as notes automatically.
Each note is a small JSON file in `notes/`; audio and transcripts stay where
they always were.

What Scribe deliberately doesn't copy from Granola: calendar sync, sharing,
billing, and AI-written meeting summaries (those need a language model —
local or cloud — which is a separate decision).

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

`config.json` is gitignored because it holds your Pocket API key and the
remote's auth token. Don't commit it.

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
