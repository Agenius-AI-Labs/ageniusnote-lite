# AgeniusNote Lite

Tiny, fast, offline voice-to-text for Windows and macOS. Press a hotkey, talk, your words get pasted into whatever window you were working in. No cloud, no account, no wake word, no LLM parsing. Just `faster-whisper` doing pure speech-to-text on your machine.

Made by [Agenius AI Labs](https://ageniusailabs.com).

![AgeniusNote Lite — minimal, hotkey-driven, fully local dictation](docs/screenshots/hero.png)

## Install

Grab the latest release from the [Releases](https://github.com/Agenius-AI-Labs/ageniusnote-lite/releases) page.

### Windows

1. Download `AgeniusNoteLite-Setup-x.y.z.exe`.
2. Run the installer. Default install path is `%LocalAppData%\Programs\AgeniusNote Lite`.
3. Optional during setup: tick "Create a desktop icon" and "Launch when Windows starts".
4. Launch AgeniusNote Lite. The window stays on top so you can see the recording state.

### macOS

1. Download the DMG for your Mac:
   - **Apple Silicon (M1/M2/M3/M4):** `AgeniusNoteLite-Setup-x.y.z-arm64.dmg`
   - **Intel:** see [CHANGELOG.md](CHANGELOG.md) under v1.0.1 "Known limitations". The Intel build path is wired and ready; the DMG will be attached to the v1.0.1 release page as soon as a GitHub Actions Intel runner becomes available. If you need Intel sooner, build from source (see below) or file an issue.
2. Open the DMG, drag `AgeniusNote Lite.app` into `Applications`.
3. **First launch:** the v1 release is not yet code-signed, so macOS Gatekeeper will block it. Right-click → Open (don't double-click) the first time, click **Open** in the dialog. Or run once from Terminal: `xattr -d com.apple.quarantine "/Applications/AgeniusNote Lite.app"`.
4. First time you trigger the hotkey, macOS will ask for **Accessibility**, **Input Monitoring**, and **Microphone** permissions. Grant all three in System Settings → Privacy & Security. The app reads no data, but the OS requires explicit permission for global hotkeys and microphone access.

First-run note: faster-whisper downloads the `base.en` model (~150 MB) on the first transcription. After that it works fully offline.

## Use it

| Action | Result |
|---|---|
| Press **Ctrl+Alt+M** while focused on VSCode, Cursor, your terminal, a browser, anything | Recording starts. The window pulses red. |
| Press **Ctrl+Alt+M** again | Recording stops, transcribes, and the text is pasted into your focused window. |
| Click **Record** in the lite window | Records into the lite window's notepad (no auto-paste). |
| **Auto-paste on/off** | Only affects hotkey sessions. Off = clipboard only, you paste manually. |
| **Copy** / **Clear** | Standard. The clipboard is also set automatically on every transcription. |

## Configure

Click the **Settings** button in the Lite window. The dialog lets you change the global hotkey, Whisper model, device (CPU / CUDA / auto), and the default auto-paste state. Changes apply immediately — no restart, no rebuild. The hotkey field is a capture widget: click it, press the chord you want, and the new binding takes effect when you click OK. If you swap the model or device, the new one is warmed up in the background so your first recording afterwards is still snappy.

Settings persist to a JSON file alongside the OS's standard per-user config location:

| Platform | Path |
|---|---|
| Windows | `%APPDATA%\AgeniusNote Lite\config.json` |
| macOS | `~/Library/Application Support/AgeniusNote Lite/config.json` |
| Linux | `~/.config/ageniusnote-lite/config.json` |

Environment variables are still honored as a fallback (config.json wins if both are set), which makes it easy to manage installs centrally if you want to:

| Variable | Default | Purpose |
|---|---|---|
| `VN_LITE_MODEL` | `base.en` | Any faster-whisper model name. `tiny.en` (fastest), `base.en` (default), `small.en`, `medium.en`, `large-v3` (slowest, best). |
| `VN_LITE_HOTKEY` | `<ctrl>+<alt>+m` | pynput hotkey string. Examples: `<f9>`, `<alt_r>+v`, `<ctrl>+<shift>+;`. |
| `VN_LITE_DEVICE` | `cpu` | Device strategy: `cpu` (small installer, default), `cuda` (NVIDIA GPU if CUDA libs exist), or `auto` (try CUDA then CPU). |

## GPU acceleration (optional)

CPU is the default — it's reliable, fast enough for `base.en` and `small.en`, and doesn't require a multi-gigabyte download. If you have an NVIDIA GPU and want to use `medium.en` or `large-v3` at real-time speeds, you'll need CUDA's cuBLAS + cuDNN runtime libraries. The easiest way is to install them as pip wheels into the same Python environment where Lite runs:

```powershell
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

Lite auto-discovers the wheel DLLs at startup (no PATH edit, no system CUDA Toolkit install needed) and falls back to CPU transparently if anything goes wrong. After installing, open Settings and switch **Device** to `cuda`. The status bar will read `… cuda/float16` on a successful transcription; if CUDA fails at runtime, the message includes the underlying error so you can act on it.

## If the hotkey doesn't fire

Far and away the most common issue. Symptom: you press the hotkey and the Lite window doesn't pulse, nothing happens. The cause is almost always **hotkey collision**, another app installed a global binding on the same chord first and is consuming the keystroke before Lite's listener sees it. Common culprits on Windows include VS Code (and its AI-chat extensions like Codex, Copilot, Cursor), Discord push-to-talk, Steam overlay, OBS, and various productivity launchers. On macOS, Spotlight, Raycast, Alfred, and built-in accessibility shortcuts will all happily eat a chord.

Quick fix: open Settings, click the hotkey field, and press something less contested. **F9** is almost always safe, as are **Ctrl+Shift+;** and **Alt+V**. The new binding takes effect as soon as you click OK — no relaunch needed. If you want to keep `Ctrl+Alt+M`, open the offending app's keybindings UI and unbind it there instead; the change to whichever app you go through propagates immediately. macOS users: if the hotkey still doesn't fire after changing it, double-check that AgeniusNote Lite is granted both **Accessibility** and **Input Monitoring** under System Settings → Privacy & Security. Missing either permission produces the same silent-failure symptom as a collision.

## How it works

- Audio capture: `sounddevice` (PortAudio) records 16 kHz mono in a background thread.
- Speech-to-text: `faster-whisper` with VAD on. Defaults to CPU int8 for reliability and small installer size. Set `VN_LITE_DEVICE=cuda` to opt into GPU; if CUDA load fails at runtime, Lite retries once on CPU.
- Hotkey: `pynput` global low-level keyboard hook. Hotkey toggle does NOT activate the lite window so your previous focus is preserved.
- Paste: on transcribe complete, clipboard is set, the original foreground HWND is restored via the Win32 AttachThreadInput trick, then `Ctrl+V` is sent.

No data leaves your machine.

## Build from source

Requires Python 3.11+.

```bash
git clone https://github.com/Agenius-AI-Labs/ageniusnote-lite.git
cd ageniusnote-lite
pip install -r requirements.txt

# Dev run (no installer)
python voice_notes_lite.py
```

### Build Windows installer

Requires [Inno Setup 6](https://jrsoftware.org/isdl.php).

```powershell
./packaging/build.ps1
# Output: dist-build-<stamp>/installer/AgeniusNoteLite-Setup-<version>.exe
```

### Build macOS .dmg

Requires Xcode command-line tools and (optionally) `create-dmg`:

```bash
brew install create-dmg   # optional; hdiutil fallback if missing
./packaging/build.sh
# Output: dist-build-mac-<stamp>/AgeniusNoteLite-Setup-<version>-<arch>.dmg
# arch defaults to the host's architecture (arm64 on Apple Silicon, x86_64 on Intel).
# Set ARCH=arm64 or ARCH=x86_64 to override the label.
```

The packaging spec excludes PyTorch, transformers, and other heavy deps that aren't actually used (faster-whisper runs on CTranslate2). Final installer is ~100 MB.

## Project docs

- [CHANGELOG.md](CHANGELOG.md) — version history.
- [ROADMAP.md](ROADMAP.md) — what's planned for v1.1 and beyond (local TTS via Supertonic).
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to file issues and send patches.
- [SECURITY.md](SECURITY.md) — how to report security issues privately.

## License

MIT. See [LICENSE](LICENSE).

## Credits

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper), CTranslate2-based Whisper inference.
- [PySide6](https://wiki.qt.io/Qt_for_Python), Qt for Python.
- [pynput](https://github.com/moses-palmer/pynput), cross-platform input control.
- [sounddevice](https://python-sounddevice.readthedocs.io/) / [soundfile](https://python-soundfile.readthedocs.io/), audio I/O.
