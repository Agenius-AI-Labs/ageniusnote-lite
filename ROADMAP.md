# AgeniusNote Lite — Roadmap

## v1.0.0 (current)

- Click-to-dictate + global-hotkey paste-into-focused-window
- Faster-whisper STT, fully local
- Windows installer (Inno Setup) + macOS .dmg
- Cyberpunk theme

## v1.1 — Local TTS via Supertonic

**Goal:** complete the local-voice loop. Today the app does voice → text. v1.1 adds text → voice.

**Engine:** [Supertonic](https://github.com/supertone-inc/supertonic) — MIT-licensed, ONNX-based, 99M-parameter on-device TTS. 31 languages, 44.1 kHz output, `pip install supertonic`. Mirrors the same "small, fast, fully offline" shape that faster-whisper gives us for STT.

**Features:**

1. **Read selection hotkey.** New global hotkey (default `Ctrl+Alt+R` on Windows, `Cmd+Opt+R` on macOS). Select text anywhere → press hotkey → Supertonic speaks it through the default output device.
2. **Read clipboard hotkey.** Companion mode — speak whatever's currently on the clipboard. Useful for AI chat replies, articles, code review comments.
3. **Voice picker.** Surface Supertonic's preset voices (M1-M5, F1-F5) in a small voice settings dropdown.
4. **Stop-speaking key.** Press the hotkey again to interrupt mid-utterance.

**Implementation notes:**

- New module `voice_notes_lite_tts.py` or inline into `voice_notes_lite.py`.
- Reuse the existing `HotkeyBridge` infrastructure. Register two more global hotkeys alongside `Ctrl+Alt+M`.
- For "read selection" we need to grab the selection from the foreground app first. On Windows: capture the OS clipboard, send `Ctrl+C`, read clipboard, restore prior clipboard. On macOS: `Cmd+C` + NSPasteboard read.
- Audio output: `sounddevice.play()` against the raw WAV array Supertonic returns.
- Model download path: bundle nothing, let Supertonic download from Hugging Face on first run (same UX as faster-whisper today).

**Size impact:** Supertonic ONNX model is ~99M params → ~400 MB on disk. Bigger jump than the faster-whisper base model. Document clearly. Consider gating behind a "TTS enabled" toggle so users who only want dictation don't pay the download cost.

## v1.2+ candidates (unprioritized)

- **Repo cleanup.** Inline the `Recorder` class from `voice_notes_v3/core/audio.py` so the OSS repo is fully self-contained, drop the `voice_notes_v3/` subpackage from the public repo.
- **Linux support.** Add a Linux paste path (X11 / Wayland) and a `.AppImage` build.
- **Configurable hotkey UI.** Replace the `VN_LITE_HOTKEY` env var with a settings dialog that captures keypresses.
- **System tray icon.** Hide-to-tray when minimized so hotkey still works without a visible window.
- **Per-app profiles.** Detect the focused app and switch behavior (e.g. strip filler words in code editors, keep them in chat apps).
- **Language picker.** Whisper supports many languages; expose `medium` / `large-v3` and a non-English model selection.
- **Apple Developer signing + notarization.** Eliminates the first-launch Gatekeeper warning on macOS.
