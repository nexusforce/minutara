# PyInstaller spec — builds dist/Scribe/ containing:
#   Scribe.exe      the app (no console window; double-click to open)
#   scribe-cli.exe  the same app for a terminal
#   _internal/      Python, the libraries and the web interface
#
# Build:  python -m PyInstaller build/scribe.spec --noconfirm --clean
# (build/build_exe.ps1 does this, and GitHub Actions does it on every merge.)
#
# A folder, not a single .exe on purpose: the speech libraries are a few
# hundred MB, and a one-file .exe would unpack all of that to a temp folder on
# every launch — slow, and a magnet for antivirus false positives. Your
# config, notes, recordings and the speech model live next to Scribe.exe.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent  # noqa: F821  (SPECPATH is provided by PyInstaller)
ICON = ROOT / "build" / "scribe.ico"

datas = [(str(ROOT / "scribe" / "web"), "scribe/web")]
binaries = []
hiddenimports = collect_submodules("scribe") + [
    "qrcode.image.svg", "clr", "webview.platforms.winforms", "webview.platforms.edgechromium",
    "pycaw.api.audiopolicy", "pycaw.api.mmdeviceapi", "pycaw.constants"]

# Libraries that load DLLs, models or data files at runtime: bring everything.
for pkg in ("faster_whisper", "ctranslate2", "onnxruntime", "av", "soundfile",
            "_soundfile_data", "pyaudiowpatch", "tokenizers",
            "webview", "pythonnet", "clr_loader",   # the app window (WebView2 via .NET)
            "pycaw", "comtypes"):                    # meeting detection (Windows audio sessions)
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:  # not installed on this platform (e.g. pyaudiowpatch off Windows)
        print(f"scribe.spec: skipping {pkg}: {e}")

# Some libraries look up their own version through package metadata.
for dist in ("faster-whisper", "ctranslate2", "huggingface-hub", "tokenizers", "tqdm",
             "requests", "packaging", "filelock", "numpy", "anthropic", "openai",
             "httpx", "pydantic", "flask", "werkzeug", "jinja2", "qrcode", "pywebview",
             "pythonnet", "clr_loader"):
    try:
        datas += copy_metadata(dist)
    except Exception:
        pass

EXCLUDES = ["tkinter", "matplotlib", "IPython", "pytest", "torch", "tensorflow"]


def analysis(script):
    return Analysis(  # noqa: F821
        [str(ROOT / "build" / script)],
        pathex=[str(ROOT)],
        binaries=binaries,
        datas=datas,
        hiddenimports=hiddenimports,
        excludes=EXCLUDES,
        noarchive=False,
    )


app_a = analysis("entry_app.py")
cli_a = analysis("entry_cli.py")


def exe(a, name, console):
    return EXE(  # noqa: F821
        PYZ(a.pure),  # noqa: F821
        a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        console=console,
        icon=str(ICON) if ICON.exists() else None,
        upx=False,  # UPX-packed DLLs trip antivirus far more often
    )


coll = COLLECT(  # noqa: F821
    exe(app_a, "Scribe", console=False),
    app_a.binaries, app_a.datas,
    exe(cli_a, "scribe-cli", console=True),
    cli_a.binaries, cli_a.datas,
    name="Scribe",
    upx=False,
)
