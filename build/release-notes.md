## Install

1. Download **Scribe-windows.zip** below.
2. Right-click the zip → **Properties** → tick **Unblock** → OK. (Optional, but it saves a Windows warning.)
3. Extract it somewhere permanent, such as `C:\Scribe` or a USB stick.
4. Double-click **Scribe.exe**.

The first time, Windows may say "Windows protected your PC" because the app isn't code-signed. Click **More info → Run anyway**. Windows Firewall will also ask once: allow it on **Private** networks, so your phone can reach the remote.

Your settings, notes and recordings are stored next to Scribe.exe. To update, extract the new zip over the old folder: your data stays.

Runs on 64-bit Windows 10/11, including ARM laptops (through Windows' built-in x64 emulation). The first transcription downloads the speech model (~500 MB for `small.en`).

Something not working? Run `scribe-cli.exe doctor` in the folder: it checks every part Scribe needs and says which one failed.
