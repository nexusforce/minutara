## Install

1. Download **Minutara-windows.zip** below.
2. Right-click the zip → **Properties** → tick **Unblock** → OK. (Optional, but it saves a Windows warning.)
3. Extract it somewhere permanent, such as `C:\Minutara` or a USB stick.
4. Double-click **Minutara.exe**.

The first time, Windows may say "Windows protected your PC" because the app isn't code-signed. Click **More info → Run anyway**. Windows Firewall will also ask once: allow it on **Private** networks, so your phone can reach the remote.

Your settings, notes and recordings are stored next to Minutara.exe, and a readable copy of every meeting is kept in its `Meeting notes` folder. To update (or go back a version): quit Minutara, delete `Minutara.exe`, `minutara-cli.exe` and the `_internal` folder, then extract the zip into the folder that contains your Minutara folder. Your data stays.

**Coming from Scribe** (this app's earlier name, up to 1.0.21): rename your `Scribe` folder to `Minutara`, delete `Scribe.exe`, `scribe-cli.exe` and `_internal` inside it, then extract this zip next to it. Your meetings and settings carry over; re-pin Minutara.exe to the taskbar.

Runs on 64-bit Windows 10/11, including ARM laptops (through Windows' built-in x64 emulation). The first transcription downloads the speech model (~500 MB for `small.en`).

Something not working? Run `minutara-cli.exe doctor` in the folder: it checks every part Minutara needs and says which one failed.
