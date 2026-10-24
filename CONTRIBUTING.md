# Contributing to Minutara

Thanks for helping. Bug reports, ideas and pull requests are all welcome.

## Reporting a bug

Open an issue with the **Bug report** form. The most useful things to include:

- the version (Settings → About, or `minutara-cli --version`)
- what you did, what you expected, and what happened instead
- the output of `minutara-cli doctor` (run it in the Minutara folder)
- the relevant part of `minutara.log` (Settings → About → View log)

**Remove anything private first.** The log can contain meeting titles and file
names. Never paste API keys, `config.json` or `granola_auth.json`.

## Running from source

Minutara is Python (3.12) with a plain HTML/CSS/JS front end (no build step).

```powershell
git clone https://github.com/nexusforce/minutara
cd minutara
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m minutara.main serve --open
```

Recording needs Windows (WASAPI loopback). The tests run on any OS.

## Tests

The suites in `tests/` run Minutara's real code against fake audio devices, a
fake Whisper model and fake cloud services, so they need no microphone, model
download or API keys:

```bash
pip install numpy flask requests qrcode playwright
python -m playwright install chromium     # for the browser tests
python tests/run_all.py                   # or: --no-ui, or a name filter like: export
```

CI runs them on every pull request, then builds `Minutara.exe` on Windows and
tests the packaged app (window, second launch, meeting detection, floating
recorder, transcription with a real model). Please add or update a test with
any change in behaviour.

## Pull requests

- Keep each pull request to one change, and describe what it does and why.
- Match the existing style: small functions, comments that explain *why*,
  messages in plain language a non-technical person understands.
- Anything that sends data off the computer must be **off by default** and say
  clearly, where it's switched on, what goes where.
- Never commit recordings, transcripts, notes or keys, even made-up ones that
  look real. Test data should be obviously fictional.
- UI changes: include a screenshot, and check both themes and a phone-width
  window (the phone remote uses the same page).

## Project layout

| Path | What |
|---|---|
| `minutara/capture.py` | Recording: microphone + speaker loopback into one stereo WAV |
| `minutara/transcribe.py`, `stt.py` | Whisper, live transcription, transcription services |
| `minutara/server.py` | The local API and app server |
| `minutara/web/` | The app's pages (`index.html`, `app.js`, `app.css`, `widget.html`) |
| `minutara/window.py`, `widget.py` | The app window and the floating recorder |
| `minutara/meetings.py` | Meeting detection |
| `minutara/ai.py` | AI notes and Ask, per provider |
| `minutara/export.py`, `backup.py` | Export, the Meeting notes folder, backup |
| `minutara/granola.py`, `pocket_client.py`, `usb_ingest.py` | Imports |
| `build/` | The Windows build (PyInstaller) |
| `tests/` | Test suites and fakes |

By contributing you agree that your contribution is licensed under the
project's [MIT License](LICENSE).
